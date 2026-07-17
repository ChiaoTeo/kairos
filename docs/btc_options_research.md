# BTC 期权期限溢价与 Skew 研究协议

## 数据源

- 主源：Deribit `history.deribit.com` 匿名 BTC option trades，长期成交型曲面；
- 交叉验证：Binance Data Vision `EOHSummary/BTCUSDT`，2023-05-18 至 2023-10-23 小时级 bid/ask、IV 和 Greeks；
- 现货标签：Binance BTCUSDT 日线；
- DVOL 仅作 30 日隐波指数对照，不参与多期限 skew 主结论。

Deribit 历史成交不是完整盘口。成交型 skew 可用于长期预测性研究，但不能直接当作可执行策略价格。策略 PnL 只使用有 bid/ask 的 Binance 子样本，并独立标注数据跨度不足。

## 受管数据集

```text
derivatives.option_trades.crypto.deribit.btc
derivatives.option_quotes.crypto.binance.btc-usdt.1h
features.volatility_surface.btc.deribit-trade-term-skew.1d
features.volatility_surface.btc.term-skew.1h
```

旧的带 `.v1` 名称仅作为迁移 Alias；`v1` 属于 Schema/Transform Version，不属于 Logical Product。

固定期限为 7、14、30、60、90 日。成交型曲面使用成交时 index price 作为 forward 代理，以 Black-76 delta 定位 10Δ、25Δ 和 ATM；每节点至少两笔成交，最大 delta 距离 0.12，节点使用成交量加权中位数，期限方向只允许在相邻到期之间按总方差插值，不外推。

## 预注册研究

### `btc_term_vrp_v1`

比较每个固定期限 ATM IV 与相同 horizon 的未来 close-to-close RV，同时报告 volatility-point VRP、variance risk premium、IV>RV 比例、overlap-aware block bootstrap 和 Newey-West t。按时间 70%/30% 切分，每期限 test 至少 50 个有效标签。

### `btc_skew_predictability_v1`

主指标为 `25Δ Put IV - ATM IV`。开发集冻结 80% 分位阈值，在 test 检验未来同期限 skew change 是否小于零。每期限 test 至少 20 个 high-skew 观测。

### `btc_skew_spread_backtest_v1`

30D high-skew 信号下卖 25Δ Put、买 10Δ Put，持有 7 天。入场使用 short bid / long ask，退出使用 short ask / long bid，计四次腿手续费。Binance 公开盘口跨度不足 252 天时结论固定为 `DATA_NOT_READY`。

### `btc_deribit_skew_spread_trade_proxy_v1`

Deribit 长历史没有免费同步 bid/ask，因此另做成交代理：开仓短腿只使用 sell-direction 成交，长腿只使用 buy-direction 成交，平仓方向反转；日内价格使用成交量加权中位数。它用于检查长期方向一致性，状态明确为 trade proxy，不能替代盘口回测。

### `btc_deribit_skew_spread_daily_delta_hedged_v1`

对上述固定两腿每日使用同合约实际成交 IV 和 Black-76 重算 Delta，以 BTC 线性永续/期货代理将净 Delta 调回零。计 7 bps 换手成本，Funding 暂未纳入；同时报告零对冲成本敏感性、上涨/下跌子样本和 stale IV 天数。

`btc_deribit_skew_spread_delta_threshold_sensitivity_v1` 进一步比较 0–0.30 BTC 的再平衡阈值，报告均值、换手、最差交易和 20% Expected Shortfall。网格使用同一 test 样本，只能作探索性风险权衡，不能挑选后宣称样本外最优。

## 运行

```bash
python3 -m trading data plan \
  --dataset derivatives.option_trades.crypto.deribit.btc \
  --provider deribit --venue deribit \
  --start 2021-01-01T00:00:00+00:00 --end 2026-07-01T00:00:00+00:00
python3 -m trading data acquire \
  --dataset derivatives.option_trades.crypto.deribit.btc \
  --provider deribit --venue deribit \
  --start 2021-01-01T00:00:00+00:00 --end 2026-07-01T00:00:00+00:00
python3 -m trading features build --feature-set btc-deribit-trade-skew-v1
python3 -m research.btc_term_vrp.study
python3 -m research.btc_skew_predictability.study
python3 -m research.btc_deribit_skew_spread.study
python3 -m research.btc_deribit_skew_spread.hedged
python3 -m research.btc_deribit_skew_spread.threshold_hedge

python3 -m trading data plan \
  --dataset derivatives.option_quotes.crypto.binance.btc-usdt.1h \
  --provider binance --venue binance \
  --start 2023-05-18T00:00:00+00:00 --end 2023-10-24T00:00:00+00:00
python3 -m trading data acquire \
  --dataset derivatives.option_quotes.crypto.binance.btc-usdt.1h \
  --provider binance --venue binance \
  --start 2023-05-18T00:00:00+00:00 --end 2023-10-24T00:00:00+00:00
python3 -m trading features build --feature-set btc-term-skew-v1
python3 -m research.btc_skew_spread_backtest.study
```

Deribit 当前期权链同样通过 `data plan` 和 `data acquire` 获取，产品为
`derivatives.option_quotes.crypto.deribit.btc.snapshots`。调用方必须提供包含当前时刻的显式
`[start,end)` 窗口；该 Connector 不会把当前快照伪装成历史回填。
