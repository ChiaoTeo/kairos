# 研究验证框架实施映射

本文记录 [`study_validation_framework.md`](study_validation_framework.md) 在项目中的生产实现和验收入口。规范定义目标和门禁，本文定义代码所有权与可执行证据。

## 核心契约

|规范对象|实现|
|---|---|
|研究注册、多维状态、资本、样本和数据缺口|`kairospy/study_platform/validation/contracts.py`|
|产品、收益来源和阶段门禁|`kairospy/study_platform/validation/protocols.py`, `gates.py`|
|有效样本量与统计功效|`kairospy/study_platform/validation/samples.py`|
|数据缺口补齐计划|`kairospy/study_platform/validation/data_gaps.py`|
|标准产物与audit hash|`kairospy/study_platform/validation/artifacts.py`|
|允许声明的结论|`kairospy/study_platform/validation/claims.py`|
|测试窗口消耗|`kairospy/study_platform/validation/test_windows.py`|
|项目治理审计|`kairospy/study_platform/validation/audit.py`|
|StrategySpec与EconomicIntent|`kairospy/domain/strategy_contract.py`|
|ExecutionPolicy|`kairospy/execution/policy.py`|
|策略到EconomicIntent适配|`kairospy/strategies/runtime.py`|
|EconomicIntent到订单计划|`kairospy/execution/strategy_planner.py`|
|资本分配和策略虚拟持仓|`kairospy/risk/portfolio_governance.py`, `strategy_positions.py`|
|Maker FIFO和Hybrid状态机|`kairospy/backtest/maker.py`|
|策略晋级与证据仓库|`kairospy/strategies/registry.py`|
|持续监控、降额和暂停|`kairospy/orchestration/strategy_monitoring.py`|

## 数据治理

Catalog中的受管数据集除`schema.json`、`lineage.json`、`coverage.json`和`manifest.json`外，必须包含`capabilities.json`。能力模板位于`kairospy/data/capabilities.py`，所有BTC pipeline和feature builder在写数据时同步写入该文件。

迁移已有数据：

```bash
# Capability metadata is written and frozen by each dataset release pipeline.
```

能力文件决定研究最高层级。缺少size、同步多腿、结算或生命周期时，天数达标也不能通过L4。

## 研究治理

版本化研究目录为：

```text
data/studies/<study_id>/<version>/
  study_spec.json
  data_capabilities.json
  data_quality.json
  sample_sufficiency.json
  data_gap_plan.json
  capital_spec.json          # 策略映射研究
  execution_spec.json        # 有执行假设时
  test_usage.json
  results.json
  REPORT.md
  audit.json
```

BTC历史研究迁移命令：

```bash
```

`data/studies/test_window_registry.jsonl`登记已经使用的测试窗口。`decision_oos`研究不能与其他已消耗窗口重叠。

## 策略治理

生产策略目录为：

```text
data/strategies/<strategy_id>/<version>/
  strategy_spec.json
  execution_policy.json
  promotions.jsonl          # DRAFT以上阶段
  promotion-bundles/<stage>-<hash>/manifest.json
  manifest.json
```

`strategy check-promotion` 会用同一套 promotion gate、evidence hash 和生命周期顺序检查做只读预检，
不修改 Strategy Release。`strategy promote` 会校验输入 evidence 的 SHA-256，评估 promotion gate，并写入一个独立
`promotion-bundles/<stage>-<hash>/manifest.json`。该 manifest 记录 from/to、StrategySpec hash、
evidence paths/hash、审批人、资本上限、rollback 条件、gate 结果和 bundle hash；`promotions.jsonl`
保留兼容审计记录并引用该 bundle。

外部阶段的 gate 明确拒绝 fixture 冒充：`PAPER_APPROVED` 需要非 fixture 的 decision-OOS L5
证据和 Paper/Testnet readiness evidence；`LIVE_LIMITED` / `LIVE_APPROVED` 需要通过的外部
soak artifact。本地 deterministic acceptance、synthetic fixture 和 trade proxy 只能作为机制证据。

参考策略首先注册为`DRAFT`：

```bash
pyenv/bin/python -m kairospy --lake-root data study register-builtin-strategies
```

BTC铁鹰以治理 Study 结果作为hash证据晋级到`STUDY_VALIDATED`：

```bash
pyenv/bin/python -m kairospy --lake-root data study register-btc-iron-condor
```

该状态只允许进入后续可执行数据研究，不代表通过L4或允许实盘。

## 执行边界

- Strategy Model决定期限、腿、ratio、信号、退出、对冲目标和风险申请；
- Portfolio/Risk批准、缩小或拒绝风险预算，但不改变结构语义；
- Execution按`ExecutionPolicy`实现Maker、Taker或Hybrid；
- Venue API、订单幂等、恢复、对账和kill switch保留在`kairospy`；
- Maker正式回测需要sequence、增量订单簿、交易事件和可重建队列；价格触碰不产生fill；
- Hybrid在超时后按policy取消、cross remainder或立即对冲。

## 验收

治理审计：

```bash
pyenv/bin/python -m kairospy --lake-root data study governance-audit
```

完整测试：

```bash
pyenv/bin/python -m unittest discover -s tests
git diff --check
```

治理审计验证：

- 每个已准备Catalog数据集具有五个治理元数据文件；
- 每个现有研究具有至少一个完整版本化产物集；
- 每个策略注册版本具有StrategySpec、ExecutionPolicy和可验证manifest；
- 晋级策略具有promotion evidence 和 promotion bundle；
- 所有artifact hash与当前文件一致。

测试文件包括：

```text
tests/test_study_validation_framework.py
tests/test_validation_protocols.py
tests/test_governance_audit.py
tests/test_strategy_governance.py
tests/test_strategy_registry.py
tests/test_builtin_strategy_specs.py
tests/test_claims_and_test_windows.py
tests/test_maker_execution.py
tests/test_strategy_positions.py
tests/test_strategy_monitoring.py
tests/test_btc_iron_condor_strategy.py
```
