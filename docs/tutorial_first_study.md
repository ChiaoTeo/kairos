# 第一次研究：从一个假设走到可拒绝的 SMA 策略

这是一份面向第一次使用者的操作教程。目标不是证明 SMA 能赚钱，而是学会系统中最重要的工作方式：

```text
提出可证伪假设
  -> 固定输入和时间语义
  -> 探索并定义 Factor
  -> 验证 Factor 可重复
  -> 明确 Strategy 规则
  -> Backtest
  -> 根据证据接受、修改或拒绝
```

本教程使用仓库内置的 90 根确定性 BTC 小时 Bar，不联网、不需要账户，也不会下单。所有命令都从仓库根目录执行。

## 先理解：这次研究到底在研究什么

研究假设是：

> BTC 的 5 小时均线高于 15 小时均线时，下一阶段收益方向可能偏正。

把它拆成系统对象：

| 对象 | 本例内容 | 它不负责什么 |
| --- | --- | --- |
| Study | 记录假设、输入数据、时间区间和研究版本 | 不直接下单 |
| Factor | `fast_sma`、`slow_sma`、`spread` | 不决定仓位和交易成本 |
| Strategy | `spread` 正负变化映射为目标交易意图 | 不重新计算另一套 Factor |
| Run | 在某个模式下运行固定 Strategy Release | 不允许悄悄改变研究定义 |

研究阶段允许快速试验，但一旦要让策略、回测或实盘消费，就必须固定输入、参数、实现和版本。这就是“探索灵活、晋级固定”。

## 最短入口：一条命令开始

第一次使用时，不需要手写 Dataset hash、时间字段和起止区间：

```bash
./pyenv/bin/python -m kairos tutorial sma
```

它会在 `example-output/first-research` 创建隔离的 Study Sandbox，自动解析教学 Dataset 的身份、真实内容 hash 和覆盖区间，并在输出的 `next` 字段告诉你下一条命令。该命令可安全重复运行，不会覆盖已有研究。

CLI 默认输出面向人的本地化字段视图和表格，而不是 JSON。可以显式选择语言：

```bash
./pyenv/bin/python -m kairos --lang zh-CN tutorial sma
./pyenv/bin/python -m kairos --lang en-US tutorial sma
```

脚本和 CI 才应显式请求稳定 JSON 契约：

```bash
./pyenv/bin/python -m kairos --format json tutorial sma
```

后面的章节会逐项解释这条命令替你完成了什么。

## 第 0 步：建立隔离的学习目录

```bash
export FIRST_RESEARCH_ROOT="$PWD/example-output/first-research"
mkdir -p "$FIRST_RESEARCH_ROOT"
```

后续生成的 Study、Factor、Strategy 和 Run Artifact 都会放在这里，不污染正式数据目录。

检查 CLI 可用：

```bash
./pyenv/bin/python -m kairos --help
```

看到 `study`、`factor`、`strategy`、`run` 四组命令即可继续。

## 第 1 步：先写假设，而不是先写策略

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  study create btc-sma-first \
  --dataset fixture:sma-bars-v1 \
  --hypothesis 'When the 5-hour SMA is above the 15-hour SMA, the next-period direction may be positive'
```

预期终端显示“研究工作区已创建”，并列出研究 ID、Dataset Release、内容指纹、时间区间和下一步命令。

此时只创建了可修改的 Sandbox。通过产品入口查看它，不需要直接打开内部 JSON：

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  study inspect btc-sma-first
```

检查点：你应该能用一句话回答以下问题，回答不了就不要进入下一步。

1. 预测对象是什么？本例是下一阶段方向。
2. 可用信息截至何时？本例只使用已完成 Bar 的 close。
3. 输入是哪一份不可变数据？本例是教学 fixture。
4. 什么证据会推翻假设？例如样本外无效、成本后亏损或结果不可重复。

系统会从 Dataset Release 自动取得 SHA-256、主时间字段和覆盖区间。完整的 `--input-release`、`--input-hash`、`--primary-time`、`--start`、`--end` 参数仍然保留，供需要完全显式输入的 CI 和高级场景使用。

## 第 2 步：先查看并检查绑定数据

教程启动时会把 90 根 Bar 发布为正式的 Governed Dataset Release。预览前 10 行：

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  study data btc-sma-first --head 10
```

只查看部分字段：

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  study data btc-sma-first --head 10 \
  --column available_time --column close --column volume
```

执行基础质量检查：

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  study profile btc-sma-first
```

继续的最低条件是：缺失值和重复主时间为 0，时间有序，OHLC 合法，并且 `event_time <= available_time`。

## 第 3 步：生成 DataFrame 研究脚本

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  study scaffold btc-sma-first
```

系统会在 Workspace 中生成 `research.py`。运行输出中的下一条命令，或直接使用 Python API：

```python
from kairos.study_platform import open_study

study = open_study(
    "btc-sma-first",
    root="example-output/first-research",
)

df = study.data.pandas()

print(df.head().to_string(index=False))
print(study.profile().as_dict())
```

也可以选择列，或使用其他 DataFrame 后端：

```python
df = study.data.pandas(columns=("available_time", "close", "volume"))
polars_df = study.data.polars()
arrow_table = study.data.arrow()
```

不需要导入 `fixture_sma_bars()`、遍历 `Bar`、手工转换 `Decimal` 或再次填写 Dataset ID。StudySession 会根据 Workspace 恢复固定的 release、hash、时间语义和区间。

## 第 4 步：在 Sandbox 中探索

真实工作中，这一步可以使用 Notebook、临时代码、图表和多组参数，系统不要求探索代码固定成一种写法。你至少应检查：

- 是否使用了未来数据；
- 缺失值、重复值和异常值如何处理；
- Factor 在多少观测后才可用；
- 参数为什么是 5 和 15，而不是因为回测后挑中了最好看的组合；
- 训练期、验证期和样本外区间如何划分。

现在可以在生成的 `research.py` 中自由增加 DataFrame 计算。例如：

```python
df["fast_sma"] = df["close"].rolling(5).mean()
df["slow_sma"] = df["close"].rolling(15).mean()
df["spread"] = df["fast_sma"] - df["slow_sma"]
df["next_return"] = df["close"].shift(-1) / df["close"] - 1

print(df[["available_time", "close", "spread", "next_return"]].tail(20))
```

正式的 batch/replay 一致性示例仍可直接运行：

```bash
./pyenv/bin/python examples/studies/sma_factor_lifecycle.py
```

重点不是只看 `true`，而是理解两个结果：

- `ready_observations`：慢均线需要 15 根 Bar 预热，预热前不能交易；
- `batch_replay_equal=true`：批量研究和逐事件运行得到相同 Factor，这是以后接回测和实时运行的基础。

## 第 5 步：冻结本轮研究定义

当假设、输入、时间语义和区间不再修改时，冻结 Candidate：

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  study freeze btc-sma-first
```

预期 `status` 为 `frozen_candidate`，并生成：

```text
$FIRST_RESEARCH_ROOT/study-candidates/btc-sma-first/1.0.0/
  study_candidate.json
  manifest.json
```

冻结不是“研究成功”，只是声明“请评审这一版，不要再悄悄改它”。如果要改变假设、数据区间或时间语义，应创建新版本，而不是覆盖 1.0.0。

## 第 6 步：把可复用计算晋级为 Factor Release

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  factor register-sma \
  --input-identity fixture:sma-bars-v1 \
  --fast 5 \
  --slow 15
```

输出中的 `factor_spec_hash` 是这份 Factor 定义的身份。它绑定参数、输入字段、预热长度、输出字段和实现代码。

查看正式定义：

```bash
python -m json.tool \
  "$FIRST_RESEARCH_ROOT/factors/sma-spread/1.0.0/factor_spec.json"
```

## 第 7 步：验证 Factor 在两种运行方式下相同

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  factor verify-sma \
  --fixture \
  --fast 5 \
  --slow 15
```

继续的硬门槛是：

```json
{
  "bars": 90,
  "batch_replay_equal": true,
  "ready": 76
}
```

如果 `batch_replay_equal=false`，立即停止。它意味着研究计算与事件运行计算不一致，后面的回测结果不能代表未来的 Paper/Live 行为。

## 第 8 步：越过研究与策略的边界

到这里，研究只说明“有一个定义明确且可重复的 SMA spread”。下面才开始定义如何交易它：

```bash
./pyenv/bin/python -m kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  strategy register-sma \
  --input-identity fixture:sma-bars-v1 \
  --fast 5 \
  --slow 15
```

Strategy Release 会显式绑定刚才的 `factor_spec_hash`、Strategy 实现和 Execution Policy。若你改变仓位大小、开平仓条件、资本约束或执行规则，改变的是 Strategy，不是 Factor。

## 第 9 步：运行 Backtest

```bash
./pyenv/bin/python -m kairos \
	  --lake-root "$FIRST_RESEARCH_ROOT" \
	  run backtest \
	  --strategy sma-cross-v1@1.2.0 \
	  --fixture \
	  --fast 5 \
  --slow 15 \
  --initial-cash 100000 \
  --fee-bps 10 \
  --artifact-root "$FIRST_RESEARCH_ROOT/artifacts"
```

本例当前的确定性结果大致为：

```json
{
  "bars": 90,
  "trades": 6,
  "final_equity": "74543.28979237672126049809444",
  "mode": "backtest"
}
```

这不是一个应该晋级的盈利策略。它反而示范了正确研究习惯：系统打通不等于假设成立；亏损结果也必须被如实保留。

把输出中的 `artifact` 路径保存下来：

```bash
export RUN_ARTIFACT='<把上一条命令输出的 artifact 路径粘贴到这里>'
./pyenv/bin/python -m kairos run inspect --artifact "$RUN_ARTIFACT"
./pyenv/bin/python -m kairos run artifact-replay --artifact "$RUN_ARTIFACT" --fixture
```

Replay 的 `passed=true` 只说明证据可重复，不说明策略值得交易。

## 第 10 步：做研究结论

这一步必须由研究者作出明确判断，本例建议记录为：

```text
结论：拒绝晋级。
原因：在给定 fixture、手续费和规则下，期末权益显著低于初始资本。
已确认：Factor batch/replay 一致，Strategy Run 可审计、可重放。
未确认：真实数据有效性、样本外稳定性、参数稳健性、市场冲击和外部 Paper 行为。
下一实验：使用真实 governed Dataset Release，预先固定训练/验证/样本外区间，比较参数邻域而非只挑最优参数。
```

一次研究的完成条件不是“赚到钱”，而是得到一个可复核的接受或拒绝结论。

## 第 11 步：什么时候才进入 Simulation 和 Paper

只有真实数据上的 Research Validation 和 Backtest 门槛通过后，才继续：

```bash
./pyenv/bin/kairos \
  --lake-root "$FIRST_RESEARCH_ROOT" \
  run simulate \
  --strategy sma-cross-v1@1.2.0 \
  --fixture --fast 5 --slow 15 \
  --run-root "$FIRST_RESEARCH_ROOT/runs/sma"
```

Simulation 用正式订单、成交、Ledger、持久化和重启恢复边界运行，但仍不连接外部 Venue。Paper 再验证外部行情、账户、订单状态、重连、对账和 Kill Switch。它们不能补救一个研究证据本身不成立的策略。

## 你完成本教程后应该能回答

1. Study、Factor、Strategy、Run 各自是什么？
2. 为什么 Research 可以灵活，而晋级物必须固定？
3. 为什么 `batch_replay_equal` 是硬门槛？
4. 为什么 `passed=true` 不代表策略赚钱？
5. 何时应该停止，而不是继续到 Paper/Live？

如果这五个问题都能回答，你已经完成了系统的第一条最小用户旅程。下一次不要换更复杂的策略，先把本教程中的 fixture 换成一份真实 Governed Dataset Release。

## 当前产品仍缺少的一步

当前 CLI 已打通 Study、Factor、Strategy 和 Run，但还没有一个面向研究者的统一命令来提交并展示“统计验证结果、样本切分、通过门槛和最终结论”。因此目前第 2 步的探索结果与第 8 步的研究结论仍需研究者在文档或现有 Research Validation API 中记录。

下一阶段产品改造应优先补齐：

```text
kairos study validate
kairos study inspect
kairos study conclude --decision accept|reject
```

并让 Strategy promotion 强制引用通过的 Research Validation Evidence。否则系统在工程链路上已经贯通，但对第一次使用者来说，研究过程仍会显得在 `study freeze` 与 `factor register` 之间“跳了一步”。
