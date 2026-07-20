# SPXW Put Skew 风险溢价 Study

## Study 问题

高 25Δ Put skew 是否随后均值回归，并提高 25Δ/10Δ Bull Put Spread 的保守成交 PnL？

预注册假设：当 `25Δ Put IV - ATM Put IV` 的 expanding percentile 高于 80% 时：

1. 未来固定 horizon 的 skew change 均值小于 0；
2. test split 中 25Δ/10Δ Put Spread 的平均 PnL 高于无条件基准；
3. block-bootstrap 95% CI 的下界大于 0。

Study 频率固定为每个交易日 15:30 一个决策点；分位数只使用此前 252 个交易日。策略最早在下一分钟 slice 成交，使用 50% 止盈、2× credit 止损或 3 DTE 时间退出。

任何一项不成立，假设记为 `NOT_SUPPORTED`。真实数据门禁未通过时记为 `DATA_NOT_READY`；门禁通过但统计样本不足时才记为 `INSUFFICIENT_DATA`。

Study 中的 spread 收益循环仅为 `TRADE_PROXY_ONLY`。即使统计条件通过，也只能返回
`TRADE_PROXY_SUPPORTED`；正式策略证据必须来自绑定同一 `spxw-put-skew` FactorSpec 的
`BullPutSpreadStrategy` 和 `BacktestEngine`，trade proxy 不能支持 executable、paper 或 live 晋级。

## 运行

从仓库根目录启动 Jupyter：

```bash
./pyenv/bin/pip install -e '.[notebook]'
./pyenv/bin/jupyter lab studies/spxw_put_skew/01_data_quality.ipynb
```

研究序列：

1. `01_data_quality.ipynb`：行情、IV solver、曲面和 readiness 门禁；
2. `02_surface_exploration.ipynb`：smile、skew、term structure；
3. `03_skew_predictability.ipynb`：未来 skew、spot、realized vol、尾部跌幅和 spread PnL；
4. `04_strategy_prototype.ipynb`：五组对照、保守成交和固定退出规则；
5. `05_out_of_sample_validation.ipynb`：development/validation/test 冻结验证和参数邻域；
6. `06_risk_decomposition.ipynb`：回撤、ES、年份、极端交易和 Greeks/residual 分解。

Notebook 默认读取 `config.json` 中的 dataset。当前仓库的 mock dataset 只有 4 个切片且期限不满足 7–45 DTE，并且不是真实采集，因此预期结论是 `DATA_NOT_READY`。

正式结论还要求 `collection.json` 证明数据来自非 synthetic 的 `ibkr.series` 采集会话。手工把 manifest 中的 `synthetic` 改成 `false` 不会绕过该门禁。

真实 Study 至少需要：

- 252 个以上可用决策时点；
- 每个时点动态且 point-in-time 的 SPXW chain；
- 7–45 DTE、覆盖 ATM/25Δ/10Δ 的双边报价；
- bid/ask、标的、利率以及准确 event time；
- test split 至少 20 个 high-skew 观测。

当前 IBKR series capture 是实时采集，不会凭空补齐历史期权链。必须先持续采集或接入合规的历史期权数据源，才能得到实证结论。

IB Gateway 登录后可以分多次续采同一个 dataset：

```bash
./pyenv/bin/python -m kairos study capture-series \
  --venue ibkr --environment paper \
  --config studies/spxw_put_skew/capture_config.json \
  --dataset-id spxw-put-skew-real \
  --samples 390 --interval-seconds 60 --checkpoint-samples 10 \
  --split development --append
```

每次运行都会更新合并后的 `dataset.json`，并在 `collection.json` 中追加带 content hash 的真实采集会话。重复运行同一个 chunk 不会重复写入；时间键冲突会直接失败。

`capture_config.json` 使用 IBKR realtime market data。账户必须拥有相应的 SPX/SPXW 行情权限；缺少实时订阅时应修复订阅，不能把 delayed 数据改名后绕过 stale/readiness 门禁。每天 15:30 选出的 25Δ/10Δ legs 会写入 `watchlist.json`，后续进程持续采集到 3 DTE，从而支持真实止盈、止损和时间退出验证。

随时检查距离正式验证还缺哪些条件：

```bash
./pyenv/bin/python -m kairos study readiness --dataset spxw-put-skew-real
```

在所有门禁通过之前，该命令返回退出码 `2`，Notebook 的结论状态保持 `DATA_NOT_READY`。

## 方法约束

- skew rank 只使用当前时点之前的数据；
- 合约选择只使用当前 slice 的 universe；
- 入场按 short bid / long ask，退出按 short ask / long bid；
- development/validation/test 按时间顺序 60%/20%/20% 划分；
- test 结果出来后不再调整阈值、delta 或 horizon；
- synthetic 数据只能验证 Study 代码，不能证明策略有效。
