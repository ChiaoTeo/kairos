# Binance product integration

The Binance integration is split by business product and account segment:

| Product | ExternalAccount segment / model | Market / account implementation |
| --- | --- | --- |
| Spot | `spot` product in no-margin or unified segment | Binance native REST/WebSocket |
| USDⓈ-M perpetual | `usd_m_futures` product in contract or unified segment | CCXT for common market, account and order ports |
| BTC/ETH Options | `options` product in a supported contract/unified segment | Binance Options Reference + REST + native WebSocket market stream |
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

Binance Options Reference currently discovers both BTC and ETH contracts from
`/eapi/v1/exchangeInfo`; the underlying filter is applied before the shared
Reference catalog is built. Options quotes and trades use Binance's native
Options WebSocket stream behind the Market stream port; REST remains the
Reference and fallback market-data boundary.

The unfiltered snapshot is complete for the configured Binance Options scope.
When an underlying filter is used, lifecycle reconciliation is limited to that
underlying and cannot delist the other one. Reference remains broad, while
Market subscriptions are selected by strategy universe and reconciled
dynamically.

When a contract disappears from a later snapshot, Reference distinguishes a
contract whose expiry has passed (`expired`) from a venue removal before expiry
(`delisted`). Both remain in the versioned local catalog and lifecycle history;
only active contracts participate in new dynamic market subscriptions.

For a provider whose catalog API requires one underlying per request, declare
the refresh scope in launch configuration:

```toml
[reference]
underlyings = ["BTC", "ETH"]
```

These values define the Reference refresh scope; they are not a market-data
subscription list. Strategies still select their own Universe from the shared
catalog.

For a broad option query, an optional Market-side safety budget limits how many
contracts one dynamic subscription may expand into:

```toml
[market]
max_dynamic_members = 200
```

If a later Reference refresh exceeds the budget, the existing subscription is
kept unchanged until the strategy narrows its query or the budget is raised.
