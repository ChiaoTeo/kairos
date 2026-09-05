# Kairos Market

Market owns live and replayed market observations, order books, subscriptions,
route resolution, freshness, and the current Market view. `MarketActor` is the
only mutable owner of runtime observation and subscription state.

Market does not own canonical instrument identity, provider authentication,
account state, execution policy, or workspace lifecycle. Reference supplies
canonical catalog facts through `kairos-reference-contract`; Integration and
System composition supply concrete provider connections.

## Entry points

- Other business packages use the independent `kairos-market-contract` crate.
- `kairos-market-server` parses process input and invokes Market composition.
- `kairos-market-cli` enters the standalone or connected application facade.
- Targets and tests in this Cargo package use `MarketApplication` and its
  explicit CLI/connected facades.

The reusable process facade lives under `application/process/`. It maps typed
contract commands and external observations into Market application calls and
publishes typed events/current views; it does not own a second market state or
select providers.

## Internal ownership and reuse

```text
bin -> composition -> application -> services
                         \-> domain
```

- `domain/` contains provider-neutral observations, order-book continuity,
  freshness rules, route facts, and state transitions.
- `application/` owns Market use cases, queries, replay, subscription and
  universe orchestration.
- `services/` contains the private Actor, publication, persistence and source
  mechanisms. Services are not a public business API.
- `composition/` selects concrete Reference clients, provider connections,
  stores, publishers and runtime mode.

Pure market rules are reused through domain types; same-package workflows are
reused through application use cases; cross-business callers never import the
main Market crate.

## Standalone Massive connections

Standalone Massive queries and historical downloads select an enabled Integration
provider connection for the requested product and `market-query` purpose. They
use that connection's credential, environment and endpoint, including private
endpoints; a legacy `market.providers` entry is not required. Multiple matching
connections are rejected rather than selected arbitrarily. `--credential-id`
can narrow selection, and `--endpoint` overrides the endpoint for that invocation.

## Quote venue evidence

Quote observations preserve provider-native `bid_venue_code` and `ask_venue_code`
alongside independently optional canonical `bid_venue_id` and `ask_venue_id`.
Managed Massive ingress resolves both sides in one Reference read session;
unknown mappings remain absent and reporting-only facilities cannot become
execution-side identities. Neither the listing venue nor the trade venue is a
fallback for an unknown quote side. The additive wire fields are defined in
[`quote.fbs`](../../../schemas/v2/market/types/quote.fbs), with old-layout
compatibility exercised by the contract's `v2_contract` tests.

## Verification

```text
cargo test -p kairos-market
python3 scripts/check/check_rust_layer_dependencies.py
python3 scripts/check/check_crate_layout.py
```
