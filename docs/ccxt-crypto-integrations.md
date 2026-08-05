# OKX 与 Hyperliquid 接入

OKX 和 Hyperliquid 的共性加密能力统一走 CCXT 适配层：

| 能力 | OKX | Hyperliquid |
| --- | --- | --- |
| 现货行情 | CCXT | CCXT |
| 永续行情 | CCXT | CCXT |
| 账户余额 | CCXT | CCXT |
| 持仓与未成交订单 | CCXT | CCXT |
| 下单与撤单 | CCXT | CCXT |

Hyperliquid 的 `swap` 产品 segment 会在组合层映射为 `usd_m_futures` 产品族，
但不能因此假设它与 Binance 的 USD-M Futures 账户模式完全相同；账户模式、抵押资产、
杠杆和仓位规则仍由 Hyperliquid adapter 观察并转换。OKX 的 `okex`/`ouyi` 别名会规范化为 `okx`。

```toml
[account]
id = "okx-main"
broker = "okx"
environment = "live"

[accounts.main]
ref = "okx-main"
segments = ["spot", "usd_m_futures"]

[feeds.okx_btc_swap]
venue = "okx"
market = "swap"

[feeds.hyperliquid_btc_swap]
venue = "hyperliquid"
market = "swap"
```

CCXT exchange 实例只存在于 Infrastructure。`CcxtMarketConnection`、
`CcxtAccountConnection` 和 `CcxtExecutionConnection` 都支持注入 exchange 对象，
所以测试可以使用 fake exchange，运行时也可以替换为原生适配器。交易所专有的
期权、理财或账户流能力不强行塞进 CCXT 统一接口，而是在产品边界单独实现。
