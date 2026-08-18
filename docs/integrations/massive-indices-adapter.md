# Massive Indices integration

Last reviewed: 2026-08-18.

## Boundary

Massive remains a `DataProvider`. `MassiveIndicesRestConnection` exposes a
provider-native `MassiveIndexDefinition` catalog because the shared reference model has no index
kind and adding one would force a business-module migration. Historical aggregates use
`HistoricalBarQuery`; live values and aggregates use `MassiveIndicesWebSocketConnection` and the
existing `IndexPrice`/`Bar` market facts.

## Protocol mapping

- Definitions: `GET /v3/reference/tickers?market=indices`, with bounded `next_url` traversal.
- Historical values: `GET /v2/aggs/ticker/{ticker}/range/...`; volume is intentionally absent.
- Live values: `V.{ticker}` (`T`, `val`, `t`).
- Live aggregates: `A.{ticker}` for one second and `AM.{ticker}` for one minute.
- Authentication uses a bearer header. Pagination URLs are rebased to the configured endpoint and
  any `apiKey` parameter is removed, preserving private proxies and preventing credential leakage.
- HTTP 401, 403, and 429 remain authentication, authorization/entitlement, and rate-limit failures;
  they are not flattened into transport errors.

## Evidence and remaining certification

Fixture tests cover official definition, aggregate, and live-value shapes, channel selection,
timestamp units, missing volume, and unsupported feed rejection. Composition and live plan
certification remain intentionally blank until a caller supplies credentials and explicitly opts
into a read-only smoke test.

Official references:

- <https://massive.com/docs/rest/indices/tickers>
- <https://massive.com/docs/rest/indices/aggregates/custom-bars>
- <https://massive.com/docs/websocket/indices/value>
- <https://massive.com/docs/websocket/indices>
