# Provider-native ordinary-order extensions

Document type: maintained integration evidence.

Last reviewed: 2026-08-18.

## Why these APIs are concrete

No business module currently owns a shared amend/batch/cancel-all request contract. Following the
repository abstraction rules, Integration therefore exposes typed inherent methods on each concrete
connection instead of widening `OrderCommand`. These methods neither own order state nor perform
transparent retries. A transport loss remains an outer `Indeterminate`; a confirmed batch response
retains one `Confirmed`, `Rejected`, or `Indeterminate` outcome per requested item.

## Coverage

| Provider/product | Amend | Batch submit | Batch cancel | Cancel all |
|---|---|---|---|---|
| Binance Spot | quantity reduction with keep-priority | not provided by ordinary Spot API | not claimed | immediate, scoped by symbol |
| Binance Margin | not provided by Margin REST | not provided | not provided | immediate, scoped by symbol |
| Binance USD-M | full limit replacement | 1-5 orders | 1-10 orders, one symbol | immediate, scoped by symbol |
| Binance COIN-M | full limit replacement | 1-5 orders | 1-10 orders, one symbol | immediate, scoped by symbol |
| Binance Options | not provided by ordinary Options REST | 1-5 orders | 1-10 orders, one symbol | immediate, scoped by symbol |
| Hyperliquid | native `modify` | native `order` batch | native `cancel` batch | scheduled global cancel is not exposed by the pinned SDK |
| OKX | single and up to 20 | up to 20 | up to 20 | `cancel-all-after` is a dead-man switch, not an immediate scoped cancel, so it is not mislabelled |

Provider-specific dead-man switches remain separate product work. A future Execution-owned use
case may define the common subset and then promote it to a shared capability without changing these
provider protocol mappings.

## Correctness rules

- Commands are attempted once. Timestamp rejection refreshes the clock only for the next caller
  attempt.
- Binance batches are size-bounded before signing. Futures and Options cancel batches require a
  single symbol. Margin exposes only symbol-scoped immediate cancel-all.
- Hyperliquid modifications send the complete desired limit-order state required by `modify` and
  preserve GTC/ALO/IOC plus reduce-only semantics. Market orders remain unsupported until a
  business-owned slippage policy exists.
- OKX requires either `ordId` or `clOrdId`; amend requires price and/or size; `reqId` is bounded to
  the provider limit; missing response items are indeterminate rather than rejected.
- A provider acknowledgement is not promoted to a final lifecycle fact. Order streams and detail
  queries remain authoritative reconciliation evidence for the downstream Execution owner.

Official references:

- <https://developers.binance.com/en/docs/products/spot/rest-api>
- <https://developers.binance.com/en/docs/catalog>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits>
- <https://www.okx.com/docs-v5/en/>
