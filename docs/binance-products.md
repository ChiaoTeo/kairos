# Binance product integration

The Binance integration is split by business product and account segment:

| Product | ExternalAccount segment / model | Market / account implementation |
| --- | --- | --- |
| Spot | `spot` product in no-margin or unified segment | Binance native REST/WebSocket |
| USDⓈ-M perpetual | `usd_m_futures` product in contract or unified segment | CCXT for common market, account and order ports |
| BTC Options | `options` product in a supported contract/unified segment | Binance Options REST plus polling market stream |
| Simple Earn | `earn` investment service, not a trading account model | Binance Simple Earn REST and `EarnApplication` |

Binance 的 Spot、USD-M Futures 和 Coin-M Futures 不能被无条件地假设为同一套账户接口。
传统账户模式下它们通常由不同 segment 表达；统一账户模式下则由一个 unified segment 暴露
按产品族区分的能力。具体采用哪种模式必须以账户配置和交易所观察结果为准。

## Replacement boundary

CCXT exchange objects are Infrastructure-only dependencies. `CcxtMarketConnection`,
`CcxtAccountConnection`, and `CcxtExecutionConnection` accept an injected exchange
object, so tests and alternate runtimes can provide a fake or another adapter.
The public business code only sees the project's Market, ExternalAccount, Execution, and
Earn application ports.

## Investment product boundary

Simple Earn products have a stable provider-neutral definition in the
reference catalog. The catalog owns product identity, product type, asset,
limits, APR, lock period, maturity and availability status. `EarnApplication`
owns product listing, positions, rewards, subscription and redemption. ExternalAccount
state remains the owner of balance changes and settlement entries.

The first supported product types are `simple_earn_flexible` and
`simple_earn_locked`. Staking and structured products are separate Binance
APIs and must not be routed through the Simple Earn provider.

## Credentials and live safety

Use separate Binance read and trade credentials. Futures and Options order
connections require the trade credential; account snapshots can use a read-only
credential. Simple Earn subscription and redemption are state-changing actions
and must not be invoked from a read-only account.

The initial Options market stream is REST polling because its payload is not the
same as Spot or Futures streams. It remains behind the Market stream port, so a
native WebSocket implementation can replace it without changing strategies.
