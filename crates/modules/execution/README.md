# Execution

Execution owns strategy Intent execution, deterministic algorithm runs, child-order dispatch,
exchange-facing order lifecycle, fills, commitments, command-delivery evidence, and execution audit.
Account remains the owner of balances, positions, equity, and account-side order facts; Risk remains the
owner of budgets and reservations; Integration remains the owner of provider authentication, protocol
mapping, and normalized external facts.

## Package boundary

Other business packages enter through the independent `kairos-execution-contract` package. Binaries,
composition, process adapters, and tests inside the main Execution package enter through
`ExecutionApplication`. Concrete gateways, stores, publishers, and mode selection are assembled in
`composition/`. `ExecutionActor` is the only mutable owner of Intent, AlgorithmRun, order, fill,
commitment, and reconciliation state.

```text
Execution contract / package binary
  -> ExecutionApplication
       -> deterministic domain algorithm
       -> ExecutionActor
       -> Risk admission and durable dispatch
       -> Integration OrderCommand / OrderQuery
```

The domain algorithm is provider-neutral. It consumes explicit business time and typed facts and emits
typed actions; it never sees provider clients, wire payloads, remote authentication, or SDK types.

## AlgorithmRun and durable actions

Every newly accepted non-empty Intent receives one `AlgorithmRun`. The caller must select exactly one
required `ExecutionAlgorithmPolicy`: `Immediate`, `Twap`, or `MakerTakerHedge`. Intent type validates
whether that algorithm is legal; it does not infer an algorithm. There is no default algorithm,
standalone `hedge_policy`, split-timing alias, or compatibility dispatch path. A run owns:

- an algorithm identity and version;
- a monotonic decision sequence and last business time;
- per-leg target, filled, and still-committed quantities;
- leg and run lifecycle state;
- stable action identities and their Pending, Completed, Failed, or Indeterminate outcome.

Immediate child dispatch is action-first. When a scheduled child becomes due, Application constructs a
typed algorithm input. `decide_immediate` authorizes at most one deterministic child action. Actor state
and the action identity are persisted before the scheduled request is removed or a provider command can
be sent. The existing planned child `OrderId` is part of the action, so a restart reuses the same command
identity rather than creating another order.

Provider command evidence resolves the dispatch action as follows:

| Evidence | Action result | Execution consequence |
| --- | --- | --- |
| Confirmed or explicitly Rejected | Completed | Order lifecycle carries the accepted/rejected fact |
| Proven NotSent | Failed | No transparent retry under a new identity |
| Delivery uncertain | Indeterminate | Intent and run require reconciliation |

On restore, Actor cross-checks pending actions against persisted order attempts. It repairs missing
plan/leg identity, resolves action state from delivery certainty, recomputes per-leg ledgers, and persists
the recovered snapshot. A missing `algorithm_runs` field remains readable for pre-algorithm snapshots;
backfilling historical active Intent runs belongs to the migration that enables those snapshots for new
algorithm decisions.

## Immediate and TWAP

`Immediate` authorizes each already-planned child through the durable action-first path. Ordinary
`SplitOrderPolicy` controls quantity subdivision only; it has no interval or algorithm-selection
semantics.

The kairospy strategy/control surface uses the same boundary. Its request models require one explicit
`ImmediateAlgorithm`, `TwapAlgorithm`, or `MakerTakerHedgeAlgorithm` and serialize that choice into the
required tagged contract field. The removed split interval and standalone pair `hedge_policy` are not
accepted by the Python API either, so Python and Rust callers do not enter different execution paths.
The cross-language certification test starts the Rust Conflux JSON-RPC runtime, submits the request with
the public Kairospy client, and verifies both exact idempotent replay and process-boundary rejection of a
removed field. An idempotency key may replay only an identical complete Intent payload; changing its
quantity, algorithm, route, or another field is rejected instead of being mislabeled as a duplicate.

`Twap` is an explicit single-leg algorithm with `slice_count` and `slice_interval`. Admission rejects a
TWAP Intent that also supplies split options, because TWAP alone owns slice quantity and cadence. The
planner preserves the exact requested total across deterministic slice identities. `TwapSpec` persists
the business-time start and derives every due time from the slice index, so restart resumes the same
schedule rather than reading wall clock or reconstructing timing from order options. Each due slice is
authorized and persisted as a `TwapSlice` action before venue dispatch.

## Maker-first / taker-hedge

A `MakerTakerHedge` algorithm policy requires a PairArbitrage Intent with exactly one leader and one
hedge leg. The leader requires a limit price,
is forced to post-only, and is the only leg initially eligible for dispatch. The hedge request remains a
durable dormant template and has no order identity until actual leader fills create exposure.

The normalized exposure ledger is expressed in hedge-leg quantity units:

```text
required hedge = leader fills × hedge ratio × contract multiplier
net leader fills = max(leader fills - unwind fills, 0)
unhedged filled = max(required hedge - hedge fills, 0)
unhedged after commitment = max(unhedged filled - live hedge leaves - live unwind equivalent, 0)
```

When unhedged filled quantity exceeds the configured tolerance, the algorithm prioritizes one taker
hedge over further maker work. Its quantity is exactly the unhedged amount after live hedge commitments;
the application materializes it as a market child from the dormant hedge route/account template. The
decision, action, and reconstructable child request are persisted before submission and dispatched by
the same due-order path in direct and managed runtimes. An indeterminate hedge blocks further execution
and requires reconciliation.

The algorithm's `HedgePolicy.fallback_execution_route_ids` may name an ordered set of alternative taker routes. Admission
rejects unknown, duplicate, primary-route, or statically incompatible alternatives. A route is selected
only after the preceding hedge is proven not sent or explicitly rejected; the selected route is part of
the durable action and request. Runtime readiness is evaluated at dispatch, so temporary unavailability
is another known failure that can advance to the next configured route. An indeterminate send never
advances the route chain.

`HedgePolicy.max_unhedged_duration` optionally limits non-zero filled exposure even when its quantity is
inside tolerance. Actor persists `unhedged_since`; the deterministic decision sets `next_wake_at` from
business time. Maintenance and explicit time advancement drive that deadline. A tail cannot complete
while this timer applies: at the deadline it becomes an exact taker hedge. The run completes only after
the leader target is filled, hedge commitment is zero, and residual exposure satisfies both quantity and
time policy.

Only after the primary and every configured fallback hedge route are exhausted may
`FailurePolicy::Compensate` and the hedge policy authorize an emergency leader unwind. Automatic unwind
additionally requires `max_slippage_bps`; the application uses
the latest leader fill as its reference, submits the opposite leader side as a bounded limit IOC through
normal Risk and Integration admission, and removes maker/split controls. The stable `UnwindImmediate`
action and complete child request are persisted together before dispatch, so a crash resumes the same
order identity. Unwind fills reduce net leader exposure independently and never increase plan completion.
A partially filled IOC keeps its live leaves as commitment; when the remainder becomes terminal, the
algorithm submits only the residual exposure under a new stable decision, bounded by the same compensation
breaker. Exhaustion fails closed into reconciliation.
A fully closed run terminates as `Unwound` and its Intent as failed, preserving that the intended arbitrage
did not complete. An indeterminate hedge or unwind remains reconciliation-only and is not re-executed.

## Current scope

Immediate, deterministic TWAP, and the maker-first/taker-hedge path with ordered route fallback and
price-protected emergency unwind are implemented behind the single explicit algorithm policy. Split and
maker options may still shape child templates and guardrails, but cannot select an algorithm or create a
second dispatch path. Provider transaction certification remains pending.

## Verification boundary

Execution Core tests are deterministic and provider-neutral: decisions, action identity, quantity
invariants, lifecycle, persistence, crash recovery, and reconciliation. Venue Execution tests belong at
the Integration provider boundary and must separately certify acknowledgement identity, protocol
mapping, private-stream convergence, disconnect recovery, and query reconciliation. A deterministic
provider fixture is not evidence that a live venue has been certified.

The ignored `kairospy_explicit_algorithm_round_trips_through_execution_json_rpc` library test is the
explicit Python/Rust process-contract gate. Run it with:

```text
cargo test -p kairos-execution --lib \
  integration_tests::kairospy_explicit_algorithm_round_trips_through_execution_json_rpc \
  -- --exact --ignored
```
