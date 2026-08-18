# Binance Stocks Trading Reference Catalog

## Provider contract

Official sources:

- Product and instrument model:
  <https://developers.binance.com/en/docs/products/stocks/introduction>
- REST-specific signing, limits, symbols, prices and disclaimer:
  <https://developers.binance.com/en/docs/products/stocks/general-info>
- WebSocket transport and stream topology:
  <https://developers.binance.com/en/docs/products/stocks/websocket-streams-general-info>
- Signed order example:
  <https://developers.binance.com/en/docs/products/stocks/quick-start>
- Full Binance product catalog:
  <https://developers.binance.com/en/docs/catalog>

- Base URL: `https://api.binance.com`
- Verified path: `GET /sapi/v1/equity/market/exchangeInfo`
- Authentication: `X-MBX-APIKEY`; request signing was not required by the verified catalog call.
- Observed response header: `x-sapi-used-ip-weight-1m: 1`.
- Verification date: 2026-08-11.
- Verification credential: Workspace `binance-equity-readonly`; the key is not copied into fixtures,
  logs, source, or this note.

The verified response contained `timezone` and a complete `symbols` array. A symbol row may contain
`symbol`, `tradability`, `tradabilityUpdateTime`, `overnightSupported`, `fractionable`,
`fractionableEh`, `extendedSession`, `maxNumOrders`, `stepSize`, `minQty`, `maxQty`, `minNotional`,
`maxNotional`, `multiplierUp`, `multiplierDown`, and `listingTime`. The endpoint did not provide a
primary listing venue, quote currency, settlement asset, or price tick, so Kairos does not invent
those facts.

## Kairos mapping

- The target `BinanceStocksRestConnection` directly implements
  `InstrumentCatalogQuery`, `MarketQuoteQuery`, `OrderCommand`, and `OrderQuery`.
- `BUY_SELL`, `BUY_ONLY`, and `SELL_ONLY` are active; `NONE` and `OFFMARKET` are inactive.
- Provider symbols become canonical US equity instruments in Reference.
- Reference retains Binance source provenance and the provider symbol; Execution determines
  whether a configured order-entry route exists at runtime.
- The source is opt-in and independently healthy as `binance-equity`.
- Because the catalog omits primary venue, Reference does not label every symbol as Nasdaq; venue
  enrichment must come from an authoritative listing source if consumers need that distinction.

Binance USD-M `TRADIFI_PERPETUAL` contracts are a separate catalog shape. They map to a canonical
equity-underlying perpetual, link to the corresponding US equity instrument, and ignore Binance's
2100 delivery-date placeholder. Tokenized spot symbols such as `AAPLBUSDT` remain ordinary crypto
spot instruments.

## Official capability inventory (2026-08-17)

REST uses the `/sapi/v1/equity/*` family:

- market/reference: exchange info, tokenized assets, latest quote;
- trading: place, cancel and cancel-all;
- query: open orders, order history/detail and trade history;
- tokenized operations: mint, redeem, conversion status/history;
- account prerequisite: US equity disclaimer;
- stream lifecycle: listen-key create/renew.

WebSocket uses `wss://nbstream.binance.com/equity`:

- public: price, quote, kline, calendar, tradability and trading status;
- private: `{listenKey}@orderReport`.

The stream protocol is one-way push. Stream names are encoded in the URL, with
single-stream `/ws/<streamName>` and combined-stream
`/stream?streams=<A>/<B>/<C>` forms; there is no subscribe/unsubscribe RPC.

## Target connection topology

- `BinanceStocksRestConnection` implements catalog, market, order command and
  order query capabilities. Disclaimer and tokenized operations remain typed concrete
  methods until they justify a stable Integration-owned Query/Command trait.
- `BinanceStocksWebSocketConnection` implements `MarketSubscriptionCommand` and
  `MarketDataStream` for URL-bound public streams.
- `BinanceStocksUserWebSocketConnection` owns the independent listen-key socket and implements
  `AccountStream`, `ExecutionStream`, and `ParticipantEventStream`. Listen-key renewal and event
  demultiplexing remain internal.
- Conflux manages named instances of only these concrete connections. It does
  not manage principals, stream names or capability handles.

Public market and private order-report streams use the same WebSocket endpoint
and protocol type. Whether Binance accepts both classes on one combined socket
must be proven by a contract/live test rather than inferred from URL syntax.
Composition selects one or two named `BinanceStocksWebSocketConnection`
instances from that evidence and any explicit fault/credential boundary.

## Current implementation

The catalog and quote capabilities use API-key-authenticated, unsigned query semantics. Order
entry and cancel use one-attempt signed command semantics; open orders, history, and detail use
signed query semantics. Public streams rebuild their URL-bound socket when the desired set changes,
while the independently managed user connection owns listen-key creation, renewal, and order-report
normalization. Cancel-all, trade history, disclaimer readiness, tokenized mint/redeem, and the
remaining calendar/tradability facts are still explicit follow-up slices.

Execution composition now accepts both `stocks` and the canonical equity-facing `equity` route
name. It creates separate named `BinanceStocksRestConnection` instances for command and query and a
`BinanceStocksUserWebSocketConnection` for execution events; it no longer rejects the Stocks slice
as an unmigrated blocking route.

## Provider invariants and required tests

- Supported instruments are US-listed common stocks and ETFs; symbols are bare
  uppercase tickers such as `AAPL` and `SPY`.
- The default quote asset is USDC and prices are expressed in USD.
- A US equity disclaimer must be accepted before the first order.
- Place-order has an additional 200 requests/minute per-UID limit.
- Limit prices accept at most two decimal places.
- Trading sessions include `RTH`, `EXTENDED` and `24H`; order requests use
  stock-specific time/session semantics rather than crypto defaults.
- The order-report listen key has a 60-minute TTL and must be renewed.
- WebSocket kline intervals begin at 5m; the adapter must not fabricate a 1m
  stream.
- Commands preserve delivery certainty and are never transparently retried once
  they may have been sent.
- Focused tests cover signing, validation, quota, disclaimer readiness, combined
  stream parsing, listen-key expiry/recovery, reconnect, backpressure and typed
  normalization.

P0 is the complete tradable loop: exchange info, quote/market status, public
streams, place/cancel/cancel-all, open/history/detail/trade queries, listen-key
and order report. Tokenized mint/redeem/status/history is P1.

## Upstream and license

No upstream adapter source was copied. The implementation was written from the live provider
response shape. Existing project license obligations are unchanged.

## Tests

- Real-shape catalog normalization with AAPL.
- Unknown tradability rejection.
- API-key-protected async capability mapping.
- Reference canonical equity, market, and execution-access mapping.
