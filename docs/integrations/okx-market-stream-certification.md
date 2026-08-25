# OKX public market-stream certification

## Evidence

- Environment: OKX production public REST and WebSocket endpoints.
- Retrieval date: 2026-08-25.
- Credential: none; public read-only channels only.
- Products exercised independently: Spot, Swap, expiry Futures, and Options.
- Lifecycle exercised for every product: connect, subscribe acknowledgement, matching observation,
  replacement-first reconnect, matching post-recovery observation, unsubscribe acknowledgement, and
  bounded disconnect.
- Observations: Spot trade (`BTC-USDT`), Swap mark price (`BTC-USDT-SWAP`), an active Futures quote,
  and an active BTC-USD Options `opt-summary` Greeks event. Expiring contracts were discovered from
  the public instruments endpoint instead of being embedded in the test.

The opt-in executable is
`crates/platform/integration/tests/okx_market_live.rs`:

```text
cargo test -p kairos-integration --test okx_market_live -- --ignored --nocapture --test-threads=1
```

The four tests passed against the production public service. This is `L-ro` evidence only. It does
not certify private authentication, order-book sequence continuity under a real gap, demo trading,
or transaction reconciliation.

## Provider constraints represented in code

The private OKX planner maps business feeds to typed native channel arguments. It preserves 24 of
the documented 480 subscribe/unsubscribe/login operations per connection per hour for recovery,
batches control payloads below the documented 64 KiB maximum, correlates acknowledgements per
argument, and filters multi-semantic channel output back to the requesting feed. Options
`opt-summary` uses `instFamily`, while ordinary instrument-scoped channels use `instId`.

Official source: [OKX V5 API guide](https://www.okx.com/docs-v5/en/).
