# Account module delivery boundary

## Positioning

Account is the single business state owner for account facts and account
freshness. It consumes normalized facts from Integration, applies account
invariants, persists the actor state, and publishes typed account snapshots.

The runtime flow is:

```text
Integration facts -> AccountApplication -> AccountActor -> snapshot/query
Execution fills/order observations --------^ 
```

Account does not own provider authentication, order planning, transfer/Earn
operations, or workspace process leases.

## Runtime and projection identity

One logical configured Account (`account_id`) owns one Account process, one
Actor and one current mmap projection. The Actor owns one Account domain state
per configured `segment_key`; the mmap therefore contains multiple rows with
the same `account_id` and distinct segment keys. It never merges spot, margin,
futures or options balances, positions or equity.

A Strategy launch may enable multiple logical Accounts. Its Python
`AccountApplication` aggregates one projection reader per Account; it does not
route multiple Account IDs through one shared mmap. Account server socket,
health, state, lock and snapshot resources use the same account-specific
component name so concurrent Accounts cannot overwrite each other.

## Delivered Account API

- Account identity and configured segments.
- Full and delta account snapshots.
- Balances, collateral, positions, equity, and account-side open-order facts.
- Refresh, asynchronous refresh, and reconciliation reports.
- Stream event application with duplicate/stale watermarks.
- Account fill audit ingestion and order observation ingestion.
- Explicit paper settlement through `/v1/simulated-fill`.
- Query, health, generation/event sequence, and snapshot publication.
- Account-specific market profiles and fee schedules.

`/v1/fill` is an externally observed fill fact. It records the fill without
invoking paper settlement. `/v1/simulated-fill` is the explicit paper-only
path.

## Ownership moved out of Account

- Execution owns intent/order planning and lifecycle. The Account contract no
  longer exposes `OrderPlan` or `plan_order`.
- Integration owns concrete transfer and Earn connection composition. Account
  only consumes resulting account facts.
- Workspace/System owns credentials, account configuration files, process
  lifecycle, and trade leases. Account runtime may validate a supplied lease,
  but does not own the lease state.
- Market/Reference own generic instrument and market rules. Account keeps only
  account-specific fees and account-mode observations.

## Capability rule

Account reports conservative account facts. Provider action capabilities must
come from Integration/Execution facts; Account must not infer transfer or
order support from a broker or segment string.

## Compatibility surfaces

Account administration still has a compatibility CLI because existing
workspace scripts use its register/credential commands. The persisted types
and lease records are now owned by `kairos-workspace::account`; Account only
uses them at its composition boundary. Provider money operations are no
longer part of that CLI. They are exposed by `kairos-integration-cli` and the
Python `integration` command.
