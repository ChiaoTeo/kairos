# Decision 0023: Explicit Execution venue environment

- Status: Accepted
- Date: 2026-08-26
- Scope: Execution route composition, external-order safety, venue certification

## Context

Execution previously inferred a managed route's provider environment by searching its REST endpoint
for `test` or `demo`. REST and private-stream hosts are independent, some official non-live hosts do
not contain those words, and OKX demo uses the production host with different authentication semantics.
Inference could therefore mislabel evidence or combine a non-live REST route with a default live
private stream.

## Decision

Every Execution route declares exactly one closed `environment`: `live`, `testnet`, `demo`, or `paper`.
Composition passes that value unchanged to every command, query, and private-stream connection for the
route. It never derives the value from endpoint text.

Simulated routes require `paper`; Binance routes accept `live` or `testnet`; IBKR accepts `live` or
`paper`. OKX `demo` is represented but rejected until simulated-trading authentication is implemented,
so a launch cannot claim demo coverage through a live session.

For a non-live external HTTP/WebSocket provider, both endpoints must be explicit. Live routes may use
the provider's live defaults. Paper/backtest launches cannot compose live, testnet, or demo routes, and
live launches cannot compose paper routes. Kairospy validates the same closed vocabulary and requires
the route environment to match the Account-owned environment. This is a breaking configuration change;
there is no endpoint-inference compatibility path.

## Consequences

- A route cannot silently inherit a live private-stream endpoint while its REST endpoint is testnet.
- Non-live certification records have an explicit environment fact rather than a hostname heuristic.
- Existing launch configurations must add `environment` to every Execution route.
- Adding provider demo/testnet support requires implementing its authentication and transport details,
  then relaxing the provider-specific admission rule with certification evidence.

## Implementation anchors

- Route value and provider admission:
  `crates/modules/execution/src/composition/connections/model.rs` and `routes.rs`
- Process configuration safety:
  `crates/modules/execution/src/bin/kairos-execution-server.rs`
- Kairospy launch validation:
  `kairospy/system/apps/launch/application/configuration.py`
- Venue evidence matrix: `docs/integrations/execution-venue-certification.md`
