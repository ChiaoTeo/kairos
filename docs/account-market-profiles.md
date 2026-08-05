# Account market profiles

账户交易费率和交易模式按以下维度解析：

```text
account identity + account book + market
```

策略只读取 Account application 投影，不访问交易所连接：

```python
profile = (
    context.accounts[0]
    .book("usd_m_futures")
    .market("BTC/USDT:USDT")
    .profile
)

fee = profile.fee
fee.maker
fee.taker
fee.currency
fee.account_rule
fee.market_rule
fee.payment.discount
```

费率画像保留三类信息：

1. `AccountFeeRule`：账户 VIP、账户费率或账户权限产生的规则。
2. `MarketFeeRule`：交易品种/交易市场自身的费率规则。
3. `FeePaymentRule`：手续费币种和折扣规则，例如 BNB 抵扣。

Integration 通过 `AccountMarketProfileConnection` 查询交易所事实，Account
Usecase 将其转换为 `AccountMarketProfile`，Account Actor 通过
`RefreshAccountMarketProfileCommand` 刷新并发布
`account.market_profile.updated` 事件。策略读取的是 Actor 投影的最新快照，
而不是在策略回调中直接发起网络请求。

`AccountFeeResolution.combination` 记录交易所适配器采用的组合语义；不同交易所
不应被强制使用同一个费率合并算法。

## Live credentials and supported venues

- Binance：`api_key` + `api_secret`，私有 REST 读取 Spot 账户类型、交易对费率和 BNB Burn 状态；合约账户通过 CCXT 的 Binance 私有 REST 路由读取。
- OKX：`api_key` + `api_secret` + `passphrase`，私有 `account/config` 读取账户模式和持仓模式，`account/trade-fee` 按 Spot/Margin 的 `instId` 或 Futures/Swap/Option 的 `instFamily` 读取费率。
- Hyperliquid：官方不是 Binance/OKX 式 API key。读取使用公开的 `info` 请求和钱包地址，交易签名使用 `private_key`；`userFees` 返回用户费率、统一账户费率层以及推荐/质押折扣后的实际费率。配置因此使用 `wallet_address` + `private_key`。

三家都必须提供真实私有凭据后才能做 live 验证；仓库测试使用官方响应形状的 fixture，不把 fixture 结果冒充成真实账户验证。
