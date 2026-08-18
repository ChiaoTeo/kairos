# Binance Advanced Trading adapter provenance

## Source and license

- Provider specification: [Binance Developer Documentation](https://developers.binance.com/en/docs/catalog),
  including Portfolio Margin, Portfolio Margin Pro, Algo Trading, Copy Trading, Institutional
  Loan, Alpha Trading, and Stocks Trading.
- Machine-readable inventory: Binance `llms.txt` and `llms-full.txt`, retrieved 2026-08-18.
- No third-party adapter source was copied. Provider request fields and response envelopes are
  independently mapped into Kairos-owned types.

## Connection mapping

- Portfolio Margin and Portfolio Margin Pro are distinct REST endpoint families. Portfolio Margin
  also owns its listen-key user stream.
- Algo Trading exposes strong typed TWAP/VP command methods plus open/historical query methods on
  `BinanceAlgoTradingRestConnection`; no raw JSON operation facade is public.
- Copy Trading exposes typed lead-trader status and symbol queries.
- Institutional Loan exposes typed risk-unit queries and borrow/repay commands with command
  delivery certainty.
- Alpha Trading directly implements catalog, quote, trade, bar, order-book, subscription, and
  market stream capabilities.
- Stocks Trading owns REST, public market WebSocket, and user order-report WebSocket connections.
  Its public WebSocket is URL-bound and one-way: it does not use the Spot `SUBSCRIBE` RPC. A changed
  desired set is confirmed only after a replacement `/ws/<stream>` or `/stream?streams=...`
  handshake succeeds.
- Funding Wallet is a distinct `BinanceFundingRestConnection`. Its read operation uses Binance's
  signed POST query endpoint and implements `AccountQuery`; it deliberately has no synthetic event
  stream because Binance does not expose a Funding Wallet user-data stream for this snapshot.

## Deliberately not copied or generalized

- No universal “advanced trading” trait or operation enum.
- No raw `serde_json::Value` request/response crosses the public participant boundary.
- Provider-specific Algo, Copy, and Institutional Loan vocabulary remains on those concrete
  connections until another production provider and a current caller establish a shared semantic
  capability.
- No automatic retry of order, Algo, loan, or other state-changing commands after a possible send.

## Remaining slices

- Add focused fixture tests for each advanced response normalizer and HTTP failure classification.
- Expand Stocks calendar/tradability domain facts when a current Market caller owns their meaning.
- Add provider-specific cancel-all, tokenized mint/redeem, and institutional-loan administration
  methods when they have current callers; do not expose an untyped endpoint escape hatch.
