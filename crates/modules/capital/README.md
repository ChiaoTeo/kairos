# Kairos Capital

Capital owns funding objectives, capital demand, allocation plans, transfer
operations, reservations, recovery actions, and their lifecycle. The private
Capital Actor is the only mutable owner of this state.

Capital does not own physical balances or positions (Account), provider
authentication and normalized transport facts (Integration), or Risk budgets.
It observes owner-contract facts and turns them into Capital-owned decisions.

## Entry points

- Other business packages use `kairos-capital-contract` only.
- `kairos-capital-server` invokes Capital composition and `CapitalProcess`.
- `kairos-capital-cli` uses explicit standalone and connected application
  facades, including the confirmation-gated transfer workflow.
- Targets and tests in this package use `CapitalApplication`.

`application/process/` owns reusable process lifecycle, RPC and publication
around `CapitalApplication`; it does not own Capital business state or select
concrete provider implementations.

## Internal ownership and reuse

```text
bin -> composition -> application -> services
                         \-> domain
```

- `domain/` contains Capital entities, lifecycle state and invariants.
- `application/` orchestrates objectives, plans, operations, settlement and
  explicit CLI/connected workflows.
- `services/` contains the private Actor, persistence and external-fact
  readers. Services are implementation details, not a second facade.
- `composition/` selects concrete transfer/earn connections, persistence,
  publishers and process configuration.

## Verification

```text
cargo test -p kairos-capital
python3 scripts/check/check_rust_layer_dependencies.py
python3 scripts/check/check_crate_layout.py
```
