# Massive Reference catalog adapter provenance

## Sources and license

- Provider contract: Massive REST reference endpoints for stock tickers and
  option contracts (`/v3/reference/tickers` and
  `/v3/reference/options/contracts`).
- Upstream adapter source copied or translated: none.
- Third-party source-code license obligations introduced by this slice: none.

## Kairos mapping

- Integration owns bearer authentication, provider pagination, cursor parsing,
  HTTP failure classification, and normalized `ExternalInstrumentCatalog`
  facts.
- Reference owns the durable cursor and page staging, canonical
  exchanges/assets/instruments/listings/markets, last-known-good promotion,
  lifecycle events, snapshots, and publication.
- Massive is a DataProvider, not a canonical exchange. Stock ticker
  `primary_exchange` is mapped only to the primary Listing. Option
  `primary_exchange=BATO` maps to Cboe BZX Options Listing; `OPRA` is treated
  as a consolidated network and does not create an Exchange, Listing, or
  Market. Massive reference rows never generate `market:massive:*`.
- Stock and option catalogs use the target `InstrumentCatalogQuery` on the concrete
  `MassiveRestConnection`, with provider-native request filters. Each page
  is persisted as its own SQLite staging row before its cursor is exposed as
  completed progress; only a page with no next cursor promotes a candidate.
  This avoids repeatedly serializing a growing candidate on every page.
- Authentication is carried only in the `Authorization: Bearer` header. Kairos
  does not put credentials in request URLs and strips an `apiKey` query field
  from provider-supplied continuation URLs before following them.

## Deliberately uncopied

- Provider SDK domain objects, caches, streaming runtimes, and application
  architecture.
- A generic session/operation facade. REST catalog pagination remains an async
  query capability rather than being modeled as a long-lived session.
- A default global stock-options catalog. Reference now requires explicit,
  durable underlying coverage and uses a provider key per underlying
  (`massive-options:<UNDERLYING>`) for cursors, staging and last-good facts.

## Evidence

- Local HTTP contract coverage verifies header-only authentication, including
  continuation URLs, and verifies that the request target never contains the
  credential.
- Failure/resume coverage verifies that every successful page and its cursor
  survive restart before a later page fails.
- The current workspace has exercised the real credentialed endpoint for both
  equity and option catalogs. An in-progress cursor is not reported as a
  completed acceptance run. SQLite staging bounds
  pagination progress only—the final catalog promotion is still a scale gate,
  not evidence that an all-market options universe is suitable for the
  steady-state Reference snapshot.
- A 2026-08-17 read-only live sample confirmed that AAPL has
  `primary_exchange=XNAS` while trades in the same feed report multiple venue
  codes, including Cboe BZX and FINRA ADF/TRF. A SPY option contract reported
  `primary_exchange=BATO`. The key was not copied into source, fixtures, logs,
  or this note.
- Massive REST/WebSocket market normalizers preserve provider-native trade,
  bid, and ask exchange codes, tape, TRF identity, participant timestamp, and
  TRF timestamp in `MarketVenueEvidence`. They do not construct canonical
  `MarketId`s; Market composition owns that resolution.

## Connection topology audit (2026-08-17)

- Massive is an API-key-authenticated data provider, not a trading-account provider; the target
  topology does not invent public/private account connections.
- REST uses `api.massive.com` for reference and historical queries. Source:
  <https://massive.com/docs/rest>.
- Live WebSockets use asset-class endpoints such as `wss://socket.massive.com/stocks`; the socket
  authenticates once and accepts multiple channel/symbol subscriptions. Massive documents a
  default limit of one concurrent connection per asset class. Source:
  <https://massive.com/docs/websocket/quickstart>.
- The current `MassiveConnection` is a REST/capability factory and
  `MassiveAsyncMarketStream` is the real socket owner. The target types are one
  `MassiveRestConnection` plus concrete asset-class WebSocket connections, initially Stocks and
  Options. They directly implement the redesigned Integration traits; no pass-through
  Massive-specific mirror trait is added. Equity/Option query filters do not require duplicate
  REST connections.
