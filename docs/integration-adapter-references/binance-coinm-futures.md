# Binance Coin-M Futures Account adapter reference

## Sources and license

- Official Binance Coin-M Futures REST account and position endpoints, user-data stream,
  listen-key lifecycle, `ACCOUNT_UPDATE`, and `ORDER_TRADE_UPDATE` documentation.
- Upstream engineering reference: `nautechsystems/nautilus_trader`, commit
  `d89b6c84bb742e43d3451794893320defb10f067`, Binance futures adapter paths.
- Upstream license: LGPL-3.0. No upstream source was copied for this slice.

## Kairos Account mapping

- `BinanceCoinMConnection` is a provider-native principal projection with an independent
  Coin-M REST endpoint, authenticated snapshot handle, listen-key channel, channel epoch,
  and bounded private-event queue.
- Account composition maps the configured `coin_m_futures` segment directly to the async
  snapshot and event capabilities. It does not use the removed blocking Account sync path
  or a universal provider registry.
- Account owns bootstrap, snapshot/stream barrier, duplicate and gap handling, recovery
  buffering, resync, freshness, current view, and business event publication.
- Position identity includes `(instrument_id, position_side)`, so Binance Hedge Mode LONG
  and SHORT rows for the same contract remain distinct.
- Commands are outside this Account slice. Provider order-entry delivery certainty remains
  owned by Execution and is not inferred from Account observations.

## Tests and deliberately uncopied areas

- Focused composition tests require one async snapshot and one async stream for the Coin-M
  segment and verify the product-specific REST endpoint.
- Shared futures normalizer tests cover non-zero positions and position-side preservation;
  Account Actor tests cover Hedge Mode identity and side-specific removal.
- No Nautilus domain model, cache, event bus, engine runtime, Python bindings, or generic
  session registry was adopted.

Real-account smoke testing remains opt-in because Binance account permissions and Coin-M
asset holdings cannot be represented by a deterministic default fixture.
