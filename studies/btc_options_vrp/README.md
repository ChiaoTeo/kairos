# BTC Options Volatility Risk Premium Study

这个研究把 BTC 期权研究拆成一条可复现的最小闭环：

- Deribit BTC DVOL 日线代表期权市场的 30 日期隐含波动率；
- Binance 官方公开归档的 BTCUSDT 日线计算 30 日实现波动率（无需账户或 API Key）；
- 前 70% 时间样本只用于冻结高溢价阈值（IV-RV 的 80% 分位）；
- 后 30% 时间样本检验未来 7 日 variance edge 与 DVOL 均值回归；
- 7 日 circular block bootstrap 保留部分时间相关性。

数据由 `trading` 统一管理，研究脚本不联网。首次运行：

```bash
python3 -m trading data acquire --dataset market.ohlcv.crypto.binance.btc-usdt.1d --provider binance --venue binance --start 2021-03-24T00:00:00+00:00 --end 2026-07-15T00:00:00+00:00
python3 -m trading data acquire --dataset analytics.vendor_volatility_index.deribit.btc-dvol.1d --provider deribit --venue deribit --start 2021-03-24T00:00:00+00:00 --end 2026-07-15T00:00:00+00:00
python3 -m trading features build --feature-set btc-iv-rv-v1
python3 -m studies.btc_options_vrp.study
```

检查任意受管数据集的 schema、lineage、coverage 和 manifest：

```bash
python3 -m trading data inspect --dataset market.ohlcv.crypto.binance.btc-usdt.1d
```

默认统一写入：

```text
data/source/provider=.../                   # 原始 payload + receipt
data/canonical/market/ohlcv/...             # Binance 标准 OHLCV
data/canonical/analytics/vendor_volatility_indices/... # Deribit DVOL
data/features/volatility/.../feature_set=iv_rv_v1/
data/studies/btc_options_vrp_v1/            # study spec、结果、图表、报告
```

再次运行会复用已经下载的原始文件。CLI 可用全局参数 `--lake-root /path/to/data` 改变数据根目录；研究端使用 `--data-root` 指向同一位置。公开 API 无需密钥，但研究结果不是投资建议，也不能替代包含真实期权链、成交成本和动态对冲的策略回测。
