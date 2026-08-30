# Kairos Risk

Risk owns policies, budgets, circuit state, authorization decisions and
reservation lifecycle. `RiskActor` is the only mutable owner of that state.

Risk does not own Account balances, Execution order lifecycle, Capital plans,
provider connections, or workspace launch. Callers submit typed proposals and
requirements; Risk returns typed decisions without taking ownership of the
caller's workflow.

## Entry points

- Other business packages use `kairos-risk-contract` only.
- `kairos-risk-server` invokes Risk composition and the reusable process facade
  under `application/process/`.
- `kairos-risk-cli` uses standalone validation/preview and connected control
  facades.
- Targets and tests in this package use `RiskApplication`.

## Internal ownership and reuse

```text
bin -> composition -> application -> services
                         \-> domain
```

- `domain/` contains policies, budgets, circuits, reservations and invariant
  checks; it depends only on shared semantic primitives.
- `application/` owns authorization and reservation use cases plus thin
  CLI/connected/process facades.
- `services/` owns the private Actor and persistence. It is not public reusable
  domain logic.
- `composition/` selects persistence, publication, runtime mode and concrete
  process configuration.

## Verification

```text
cargo test -p kairos-risk
python3 scripts/check/check_rust_layer_dependencies.py
python3 scripts/check/check_crate_layout.py
```
