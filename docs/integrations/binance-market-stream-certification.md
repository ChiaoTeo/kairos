# Binance public market-stream certification

## Certification record

- Provider environment: Binance production public market data.
- Retrieval date: 2026-08-25.
- Credentials and permissions: none; public REST and WebSocket endpoints only.
- Mutation scope: subscription control messages only; no account or trading mutation.
- Executable evidence: `crates/platform/integration/tests/binance_market_live.rs`.

The opt-in live suite exercised the concrete Kairos Integration connections, rather than a standalone
WebSocket client. For each product it connected, subscribed, received a normalized event, replaced the
connection and restored the same logical subscription, received another event, confirmed unsubscribe,
and disconnected.

| Product | Endpoint class | Requested evidence | Result |
|---|---|---|---|
| Spot | `stream.binance.com:9443/ws` | `BTCUSDT` trade | Passed |
| USD-M | `fstream.binance.com/market/ws` | `BTCUSDT` mark price | Passed |
| COIN-M | `dstream.binance.com/ws` | `BTCUSD_PERP` best quote | Passed |
| Options | `fstream.binance.com/public/ws` and `/market/ws` | active BTC option quote and mark price | Passed |

Options certification also exercised the current provider protocol shapes: contract-level
`bookTicker`, underlying-level `optionMarkPrice`, array-frame expansion, and filtering the underlying
firehose down to the demanded contract. The active option symbol is discovered from public
`/eapi/v1/exchangeInfo` at test time.

Run the certification explicitly with:

```text
cargo test -p kairos-integration --test binance_market_live -- --ignored --nocapture --test-threads=1
```

## Bounds

This is `L-ro` evidence for public streaming and recovery. It is not evidence for authenticated user
streams, order entry, geographic availability, a 24-hour soak, or snapshot-plus-diff order-book
continuity. Session rotation is covered with deterministic accelerated-time tests; full-duration soak
and depth continuity remain production gates in the capability matrix.
