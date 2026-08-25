# IBKR TWS / Gateway market-stream certification

## Current evidence and external gate

`IbkrMarketDataConnection` now implements both bounded quote query and managed Level-I streaming.
Focused tests cover supported-feed planning, shared physical-line recovery planning, TWS entitlement
and pacing error classification, and Market/Conflux composition. The opt-in live executable compiles,
but no running TWS or IB Gateway was available in the 2026-08-25 audit environment; therefore IBKR is
not marked `L-ro`.

Run the certification against an API-enabled, already authenticated session:

```text
IBKR_HOST=127.0.0.1 IBKR_PORT=7497 \
cargo test -p kairos-integration --test ibkr_market_live -- --ignored --nocapture
```

Optional variables select client ID, symbol, exchange, currency, and the account's market-data-line
allowance. The test performs connect, quote subscribe/receive, reconnect with logical-subscription
restoration, second receive, unsubscribe, and disconnect. The TWS username must have the applicable
live or delayed data permission. No order is submitted.

## Provider constraints represented in code

Multiple strategies requesting the same symbol share one `reqMktData` line. The configured line
allowance defaults to IBKR's documented 100; ordinary admission retains 10% headroom. Control traffic
uses 45 of the documented 50 client-to-TWS messages per second, while recovery may use the full limit
with pacing. Error 354 is an entitlement failure; errors 100, 101, and 420 are rate/capacity failures.
Because the same TWS client ID cannot support two simultaneous replacement sessions, reconnect is
necessarily disconnect-first but preserves and restores logical demand.

Official sources: [IBKR streaming market data](https://interactivebrokers.github.io/tws-api/market_data.html),
[market data types](https://interactivebrokers.github.io/tws-api/market_data_type.html), and
[IBKR Campus TWS API documentation](https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/).
