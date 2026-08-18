# Massive Futures integration

- Provider: Massive (`DataProvider`)
- Audited: 2026-08-18
- Certification: fixture-backed implementation only; no `L-ro` or `L-tx` claim
- Credential: Massive API key with the relevant Futures plan entitlement; the key is sent only in the
  `Authorization: Bearer` header

## Implemented Integration surface

| Capability | Provider endpoint |
|---|---|
| Contract catalog | `GET /futures/v1/contracts` |
| Historical bars | `GET /futures/v1/aggs/{ticker}` |
| Historical quotes | `GET /futures/v1/quotes/{ticker}` |
| Historical trades | `GET /futures/v1/trades/{ticker}` |
| Realtime quotes/trades/aggregates | Massive Futures WebSocket endpoint |

The implementation keeps Futures separate from the equity/options REST configuration because Futures uses
the `/futures/v1` contract model, nanosecond query windows, session-based bars, contract quantities, and
product/exchange identities. It does not infer a canonical venue or settlement currency.

## Reliability and entitlement behavior

- REST pagination is bounded to 10,000 pages.
- Provider `next_url` values are rewritten onto the configured endpoint and any `apiKey` query value is removed.
- Catalog pages are bounded to the provider maximum of 1,000 rows.
- Bars use at most 50,000 rows per page; quotes/trades use at most 49,999.
- HTTP 401 is `Authentication`, 403 is `Authorization`, and 429 is `RateLimited`.
- WebSocket authentication, acknowledgement buffering, reconnect subscription replay, bounded buffering,
  idle detection, and maintenance use the shared Massive socket implementation.

## Official references

- <https://massive.com/docs/rest/futures/contracts>
- <https://massive.com/docs/rest/futures/aggregates>
- <https://massive.com/docs/rest/futures/trades-quotes>
- <https://massive.com/docs/rest/futures/trades-quotes/quotes>
- <https://massive.com/docs/websocket/futures/overview>
