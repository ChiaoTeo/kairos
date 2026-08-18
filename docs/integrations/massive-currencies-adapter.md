# Massive Forex and Crypto integration

Last reviewed: 2026-08-18.

## Boundary and product separation

`MassiveCurrenciesRestConnection` is configured explicitly as Forex or Crypto. Both products use
typed spot reference facts and historical bars/quotes, but only Crypto implements historical
trades. Their WebSocket connections and channels are distinct:

| Product | Quote | Trade | 1-second bar | 1-minute bar |
|---|---|---|---|---|
| Forex | `C` | unsupported | `CAS` | `CA` |
| Crypto | `XQ` | `XT` | `XAS` | `XA` |

Forex bars are marked as quote-derived; Crypto bars are marked as trade-derived. Provider-native
symbols (`USD/CNH`, `BTC-USD`, or REST-prefixed ticker forms) are preserved. Integration does not
claim that Massive is the canonical venue for either decentralized market.

## REST behavior

- Catalog: `GET /v3/reference/tickers`, filtered by `market=fx` or `market=crypto`.
- Bars: `GET /v2/aggs/ticker/{ticker}/range/...`.
- Quotes: `GET /v3/quotes/{ticker}`.
- Crypto trades: `GET /v3/trades/{ticker}`.
- All result collections use a 10,000-page safety bound, ascending time ordering, a 50,000-row page
  limit where supported, bearer authentication, private-proxy URL rebasing, and API-key removal
  from continuation URLs.

Fixture tests cover reference currency pairs, nanosecond Crypto trades, Forex quotes, Crypto
quotes/trades/aggregates, interval-channel selection, timestamp conversion, and continuation URL
sanitization. Live entitlement certification is not claimed without explicit credentials.

Official references:

- <https://massive.com/docs/rest/forex/overview>
- <https://massive.com/docs/rest/crypto>
- <https://massive.com/docs/websocket/forex/overview>
- <https://massive.com/docs/websocket/forex/quotes>
- <https://massive.com/docs/websocket/crypto/trades>
- <https://massive.com/docs/websocket/crypto/quotes>
- <https://massive.com/docs/websocket/crypto/aggregates-per-minute>

## Flat Files decision

Flat Files are deliberately not exposed as a REST query or as an Integration-owned filesystem
workflow. Massive publishes gzip CSV objects through the S3-compatible `flatfiles` bucket at
`https://files.massive.com`; individual datasets can be tens or hundreds of gigabytes. Per the
repository ownership rules, Workspace must own destination paths, resume state, and lifecycle,
while Market owns streaming ingestion and deduplication. A later cross-platform task may compose an
S3 client with those owners and must verify object identity/ETag or checksum, expected date, CSV
header/schema version, bounded decompression memory, and resumable range transfer. Until that
caller exists, adding an Integration downloader would create an ownerless facade and is explicitly
rejected.

Official Flat Files references:

- <https://massive.com/docs/flat-files/quickstart>
- <https://massive.com/docs/flat-files/forex>
- <https://massive.com/docs/flat-files/crypto/trades>
