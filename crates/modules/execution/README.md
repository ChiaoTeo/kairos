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

Execution business time is mutable business state owned by `ExecutionActor`, not an Application cache.
It is monotonic, persisted in every snapshot after its first advance, restored before algorithm repair,
and synchronized into the private planning worker. Intent admission requires an explicit source-event
time or an already established Actor time. Maker quote refresh carries the exact decision time used for
future/stale and minimum-cadence checks; the Core has no wall-clock refresh overload. Live and paper
Conflux composition may sample processing time at its timer/control boundary and immediately persist it
through the same `advance_time` path. Backtest and replay disable that wall-clock source and advance only
from explicit replay input.

The same clock governs order attempts, Intent lifecycle transitions, reconciliation, fills, and
compensation. Network delivery order is not treated as business-time order: a late fill keeps its venue
occurrence time but is applied at the current monotonic Actor time, and a stale acknowledgement cannot
regress a partially-filled or terminal order. Fill identity deduplicates re-delivery; remote identity
conflicts fail closed. Scheduled TWAP, hedge, fallback, and unwind children are stamped when their durable
action becomes due, rather than reusing an older Intent-template timestamp.

Command acknowledgements do not invent a provider sequence. Private Conflux facts persist their actual
connection, channel, reconnect epoch, and optional participant sequence on the order/fill journal. Only
sequence regression within the same channel epoch, or an event from an older epoch, is stale; gaps are
legal because intervening channel events may belong to other orders. A new epoch permits a sequence reset.
Mutually exclusive terminal facts enter durable `AuthoritativeFactConflict` reconciliation, make the
commitment and Risk reservation uncertain, and remain fail-closed across restart until a complete query
resolves them.

## Process delivery boundary

The Contract-owned JSON-RPC Unix-socket surface carries commands and bounded operations (`health`, route
discovery, durable Order audit, reconciliation, explicit time advance, and backtest); it does not
provide a second current Order, Intent, or AlgorithmRun listing API. Aeron `execution-events` carries immutable sequenced Intent/Plan,
Order, and Fill changes. Reconciliation is expressed by the affected Intent or Order lifecycle rather
than a standalone compatibility event.

The current-view implementation is one Execution LMDB environment with independently keyed `orders`,
`intents`, `algorithm_runs`, `commitments`, `risk_reservations`, and `unknown_remote_orders` named
databases. One Actor transition updates every affected family and its applied event sequence in one
transaction. There is no aggregate snapshot reader, schema root, or fallback. The durable audit remains
the history authority. Venue open/history/detail queries are
private reconciliation inputs and cannot bypass ExecutionActor to become a public state path.

One launch instance composes one ExecutionActor and one owner-scoped current-view environment. The
Actor may execute many Intents concurrently; each Intent owns its AlgorithmRun, legs, and child Orders.
Multiple Execution actors or executor shards in one launch instance are not a supported topology.

Connected current-state commands are named `active-orders`, `active-order`, and
`unknown-remote-orders`; complete Order lifecycle evidence uses the persistent `audit` query. The LMDB
current view does not retain recent event/fill history. The removed
`snapshot/recent-order-events/recent-fills` reads are not forwarded to another path. Removed
names are not aliases.

The bounded `health` query is operational status rather than a second state listing. In addition to
required-route, writer-takeover, and outbox publication health, it derives Risk recovery readiness,
orders and Intents requiring reconciliation, unresolved remote orders, and indeterminate AlgorithmRun
actions directly from ExecutionActor truth. Any such safety blocker reports `degraded` and survives
restart; operators do not need to infer a stuck execution from logs or a terminal-looking HTTP response.
Execution JSON models accept only their current field names; the removed `provider_symbol` alias is not
a second route/fill API. FlatBuffers may retain provider-named physical fields at the wire boundary, but
they are converted once into the current `order_entry_symbol` business vocabulary.

## AlgorithmRun and durable actions

Every newly accepted non-empty Intent receives one `AlgorithmRun`. The caller must select exactly one
required `ExecutionAlgorithmPolicy`: `Immediate`, `Twap`, `PassiveLimit`, or `MakerTakerHedge`. Intent type validates
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

Order command attempts are typed as `Submit` or `Cancel`; they are not an unlabelled retry counter.
Execution persists a cancel attempt as `Indeterminate` before the provider command can leave the
process. A crash or lost response therefore blocks another cancel until a private event or bounded
query marks the attempt `Reconciled`. A provider cancellation acknowledgement is accepted only when
both local/remote identities match and the reported lifecycle is explicitly `Canceled`; HTTP success or
an `Accepted` order fact cannot authorize replacement submission.

The cross-process replace command has one portable implementation: confirmed cancel followed by a new
submit through the normal Risk and order-entry path. The requested quantity is the desired total across
the original/replacement lifecycle, so an original partial fill is subtracted before the replacement is
submitted. A terminal original order or a requested total no larger than its filled quantity is rejected.
There is no second package-level `replace` facade or provider-native amend shortcut.

On restore, Actor first restores its business clock and then cross-checks pending actions against
persisted order attempts. It repairs missing
plan/leg identity, resolves action state from delivery certainty, recomputes per-leg ledgers, and persists
the recovered snapshot. A missing `algorithm_runs` or `business_time_unix_nanos` field remains readable
for historical snapshots;
backfilling historical active Intent runs belongs to the migration that enables those snapshots for new
algorithm decisions.

An Indeterminate attempt remains immutable audit evidence, but it is not a permanent run state. A later
authoritative private event or venue query carrying the stable local order identity and remote order
identity may reconcile the current order and complete the original action. The historical attempt is not
rewritten; Actor derives the current leg/run lifecycle from the reconciled order fact and persists that
convergence before any further algorithm decision.

Actor also derives realized execution quality from its canonical orders, attempts, and fills. Every
AlgorithmRun publishes per-leg order, fill, and cancel-attempt counts; exact filled quantity, gross
notional, and optional VWAP; first submission/fill and last-fill timing; and fee totals grouped by
currency. The value is rebuilt after every synchronization and after restore, then persisted with the
run and exposed in both current-Execution and active-Intent views. Emergency unwind orders are not mixed
into ordinary leader quality, and prices or fees from different legs/currencies are not collapsed into a
false aggregate. An optional explicit per-leg `Arrival` benchmark records exact instrument, market,
price, and observation business time. Admission binds it to the executable plan leg; Actor derives exact
benchmark notional and signed implementation shortfall, where positive always means worse for the order
side. Fill delivery order, duplicate delivery, and restart do not change the result. A limit price or wall
clock is never used as a substitute benchmark.

Normalized private execution events keep those identities as separate types: a venue-assigned
`remote_order_id` is required and the submitted `client_order_id` is optional. Execution may use the
client identity to associate an event that raced the command response, but it only persists the required
remote identity as the venue order identity. There is no ambiguous `order_id` fallback or compatibility
path at this boundary.

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

## PassiveLimit quote provisioning

`QuoteProvisioning` requires an explicit `PassiveLimit` policy. `reprice_interval` owns refresh cadence
and `max_quote_age` owns quote freshness; maker order options retain only admission guardrails and cannot
select or schedule an algorithm. Kairospy and the Rust contract reject the removed Immediate quote path
and removed maker cadence fields.

A refresh is one persisted two-phase Actor transaction shared by direct and managed runtimes. Execution
first records the refresh identity, version, and exact cancel targets. It sends no replacement action
until every cancel is determinate. It then rebuilds each leg's remaining quantity from current Actor fill
truth, persists the deterministic PassiveLimit actions and complete requests, and dispatches them through
the ordinary due-order path. Cancel response loss, a competing refresh, or reconciliation state cannot
activate replacements. If a fill arrives after staging but before authorization, its quantity reduces
the replacement; duplicate delivery remains idempotent, and restart resumes the same transaction.

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

Immediate, deterministic TWAP, two-phase PassiveLimit quote refresh, and the maker-first/taker-hedge path
with ordered route fallback and price-protected emergency unwind are implemented behind the single
explicit algorithm policy. Split and maker options may still shape child templates and admission
guardrails, but cannot select an algorithm or create a second dispatch path. Provider transaction
certification remains pending.

## Verification boundary

Every configured route carries one explicit `live`, `testnet`, `demo`, or `paper` venue environment.
Execution does not infer it from endpoint text. Non-live external HTTP/WebSocket routes require both
endpoints explicitly; launch mode, provider support, and the Account environment are checked before
composition. This is a breaking boundary with no endpoint-inference compatibility path. In particular,
OKX demo fails closed until its simulated-trading authentication is implemented.

An external transaction changes the venue matrix to `T` only through a complete, secret-free record in
[`docs/integrations/execution-certifications`](../../../docs/integrations/execution-certifications/README.md).
The repository checker binds each `T` mark to submit, private event, query, fault recovery, restart,
duplicate-prevention, mapping, cleanup, and content-addressed artifact evidence. This records behavior
from the normal Execution path; it does not introduce a certification sender or alternative API.

Execution Core tests are deterministic and provider-neutral: decisions, action identity, quantity
invariants, lifecycle, persistence, crash recovery, reconciliation, duplicate delivery, late fills, and
stale acknowledgements across restart. For maker-first/taker-hedge, delivering the same incremental
leader fills in either occurrence-time order must produce the same normalized exposure and exact set of
taker quantities; duplicate delivery, a late maker acknowledgement, and restart replay cannot create a
second hedge for an already-accounted fill. Venue Execution tests belong at
the Integration provider boundary and must separately certify acknowledgement identity, protocol
mapping, private-stream convergence, disconnect recovery, and query reconciliation. A deterministic
provider fixture is not evidence that a live venue has been certified.

The repository exposes those boundaries as two independent local gates. `make execution-core-check`
runs the provider-neutral package, contract, Python contract, and Kairospy-to-Rust process boundary.
`make execution-venue-check` runs provider execution fixtures, the real local Aeron transport with
disconnect/reconnect and restart convergence, and the venue evidence checker. The latter is local
conformance evidence (`D/C/R`), not an external transaction certification (`T`). The matrix checker
rejects unknown or non-cumulative evidence marks, so `C` requires `D`, `R` requires `D/C`, and `T`
requires all three local evidence levels plus a valid transaction record.

The ignored `kairospy_explicit_algorithm_round_trips_through_execution_json_rpc` library test is the
explicit Python/Rust process-contract gate. Run it with:

```text
cargo test -p kairos-execution --lib \
  integration_tests::kairospy_explicit_algorithm_round_trips_through_execution_json_rpc \
  -- --exact --ignored
```

The local venue-transport gate starts a real Aeron driver plus mock Binance listen-key HTTP and two
WebSocket sessions. The first session disconnects after a partial fill; Conflux must obtain a new listen
key, reconnect, and deliver the remaining fill without duplicating the order or fill journal. Build the
driver and run the gate with:

```text
cargo build -p kairos-transport --bin kairos-aeron-driver
KAIROS_AERON_DRIVER_BIN=target/debug/kairos-aeron-driver cargo test -p kairos-execution --lib \
  integration_tests::managed_binance_private_stream_reconnects_and_converges_without_duplicate_fill \
  -- --exact --ignored
```
