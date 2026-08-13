# Binance Stocks Trading Reference Catalog

## Provider contract

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

- Integration exposes async `InstrumentCatalogConnection` and single-symbol
  `MarketQuote` query projections.
- `BUY_SELL`, `BUY_ONLY`, and `SELL_ONLY` are active; `NONE` and `OFFMARKET` are inactive.
- Provider symbols become canonical US equity instruments in Reference.
- Reference records Binance as an execution access and retains the provider symbol.
- The source is opt-in and independently healthy as `binance-equity`.
- Because the catalog omits primary venue, Reference does not label every symbol as Nasdaq; venue
  enrichment must come from an authoritative listing source if consumers need that distinction.

Binance USD-M `TRADIFI_PERPETUAL` contracts are a separate catalog shape. They map to a canonical
equity-underlying perpetual, link to the corresponding US equity instrument, and ignore Binance's
2100 delivery-date placeholder. Tokenized spot symbols such as `AAPLBUSDT` remain ordinary crypto
spot instruments.

## Deliberately unsupported

The quote capability uses `GET /sapi/v1/equity/market/quote?symbol=AAPL` with
the API-key header. A successful empty response body is normalized to
`Ok(None)` rather than treated as JSON `null` or a malformed response. The
query uses bounded query retry semantics. Order-entry, cancel, open-order,
history, and order-event endpoints remain unsupported.

## Upstream and license

No upstream adapter source was copied. The implementation was written from the live provider
response shape. Existing project license obligations are unchanged.

## Tests

- Real-shape catalog normalization with AAPL.
- Unknown tradability rejection.
- API-key-protected async capability mapping.
- Reference canonical equity, market, and execution-access mapping.
