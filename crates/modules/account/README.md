# Kairos Account

Account owns balances, collateral, positions, earn holdings, account-side
order/fill observations, synchronization completeness, and freshness.
`AccountActor` is the only mutable owner of those facts.

Account does not own exchange-facing order lifecycle, Risk reservations,
canonical Reference identity, provider authentication, or process launch.
Live facts have one authoritative ingress: Account-owned Integration snapshot
and event capabilities. Simulation settlement is an explicit, idempotent
exception and is rejected by live processes.

## Entry points

- Other business packages use `kairos-account-contract` only.
- `kairos-account-server` invokes concrete Account composition and the reusable
  process facade under `application/process/`.
- `kairos-account-cli` uses the composition-owned standalone facade for direct
  provider work and `ConnectedAccountApplication` for daemon-backed queries.
- Targets and tests in this package use `AccountApplication`.

## Internal ownership and reuse

```text
bin -> composition -> application -> services
                         \-> domain
```

- `domain/` owns Account entities, current business facts and invariants.
- `application/` owns refresh, reconciliation, mark-to-market and connected
  query use cases.
- `services/` owns the private Actor, provider mapping, synchronization,
  persistence workers and publication. It is not an externally reusable
  domain-logic layer.
- `composition/` selects provider sessions, credentials, runtime clients,
  stores and publishers.

Provider DTOs are converted once into Account-owned typed facts. Same-package
callers reuse application use cases; cross-business callers use the contract.

## Verification

```text
cargo test -p kairos-account
python3 scripts/check/check_rust_layer_dependencies.py
python3 scripts/check/check_crate_layout.py
```
