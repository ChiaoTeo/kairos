# Massive market-stream certification

## Current evidence and external gate

All six current products—Stocks, Options, Futures, Indices, Forex, and Crypto—have concrete
Integration connections and Market/Conflux composition routes. Fixture and local-socket tests cover
native channel planning, duplicate logical demand sharing one physical stream, final-owner release,
authentication acknowledgement, replacement-first reconnect/restore, and immediate classification of
authentication, entitlement, and capacity failures.

No `MASSIVE_API_KEY` was available in the 2026-08-25 audit environment, so this change does not add a
new live-certification claim. Existing Equities and Options `L-ro` evidence remains unchanged. The
reproducible opt-in executable is
`crates/platform/integration/tests/massive_market_live.rs`:

```text
MASSIVE_API_KEY=... \
cargo test -p kairos-integration --test massive_market_live -- --ignored --nocapture --test-threads=1
```

Options and Futures require `MASSIVE_OPTIONS_SYMBOL` and `MASSIVE_FUTURES_SYMBOL` so an active,
entitled contract is selected explicitly. Each product endpoint can be overridden with
`MASSIVE_<PRODUCT>_ENDPOINT`, which supports delayed or account-specific tiers without changing code.
The test performs subscribe, receive, reconnect/restore, second receive, unsubscribe, and disconnect.

## Provider constraints represented in code

Each product owns its native channel vocabulary (`Q/T/A/AM`, `V/A/AM`, `C/CA/CAS`, or
`XQ/XT/XA/XAS`). Options reject plans above the documented 1,000 quote contracts per connection.
The default production endpoints are `socket.massive.com` for Stocks, Options, Futures, Forex, and
Crypto, and the documented business endpoint for Indices. Massive remains a data provider rather
than a fabricated canonical venue.

Official sources: [Massive WebSocket quickstart](https://massive.com/docs/websocket/quickstart) and
[Massive WebSocket documentation](https://massive.com/docs/websocket).
