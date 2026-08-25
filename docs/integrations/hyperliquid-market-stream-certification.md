# Hyperliquid public market-stream certification

## Evidence

- Environment: Hyperliquid production public Info and WebSocket endpoints.
- Retrieval date: 2026-08-25.
- Credential: none; public read-only subscriptions only.
- Perpetual behavior: `BTC` symbol-scoped `bbo` subscribe, quote receive, replacement-first
  reconnect/restore, second quote receive, and unsubscribe.
- Spot behavior: the highest-volume active spot market was selected from `spotMetaAndAssetCtxs`,
  then L2 subscribe, snapshot receive, reconnect/restore, second snapshot receive, and unsubscribe
  completed.

The opt-in executable is
`crates/platform/integration/tests/hyperliquid_market_live.rs`:

```text
cargo test -p kairos-integration --test hyperliquid_market_live -- --ignored --nocapture --test-threads=1
```

Both tests passed against production. This is `L-ro` evidence for public Spot and Perpetual market
streams. It does not certify private address subscriptions or transaction recovery.

## Provider constraints represented in code

The private planner uses symbol-scoped `bbo` rather than the all-market `allMids` firehose. Mark,
oracle/index, funding, and open-interest demands share one `activeAssetCtx` physical subscription.
Process-shared admission uses 950 of the documented per-IP 1,000 subscriptions and 1,900 of the
documented per-IP 2,000 outgoing WebSocket messages per minute, retaining recovery headroom across
Spot and Perpetual sockets. Acknowledgement matching accepts provider-added default fields. Reconnect
restores a replacement socket first when the shared subscription budget permits the overlap; otherwise
it disconnects and restores without temporarily violating the provider limit.

Official sources: [Hyperliquid WebSocket](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket),
[subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions),
and [rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits).
