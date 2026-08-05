# Binance product integration

The Binance integration is split by business product and account book:

| Product | Account book | Market / account implementation |
| --- | --- | --- |
| Spot | `spot` | Binance native REST/WebSocket |
| USDⓈ-M perpetual | `usd_m_futures` | CCXT for common market, account and order ports |
| BTC Options | `options` | Binance Options REST plus polling market stream |
| Simple Earn | `earn` | Binance Simple Earn REST and `EarnApplication` |

## Replacement boundary

CCXT exchange objects are Infrastructure-only dependencies. `CcxtMarketConnection`,
`CcxtAccountConnection`, and `CcxtExecutionConnection` accept an injected exchange
object, so tests and alternate runtimes can provide a fake or another adapter.
The public business code only sees the project's Market, Account, Execution, and
Earn application ports.

## Credentials and live safety

Use separate Binance read and trade credentials. Futures and Options order
connections require the trade credential; account snapshots can use a read-only
credential. Simple Earn subscription and redemption are state-changing actions
and must not be invoked from a read-only account.

The initial Options market stream is REST polling because its payload is not the
same as Spot or Futures streams. It remains behind the Market stream port, so a
native WebSocket implementation can replace it without changing strategies.
