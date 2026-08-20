# Capital management boundary

Document type: current architecture.

Capital management maintains policy-driven account funding targets and turns
aggregated demand into safe, reconciled capital placement. It is not an alias
for a participant's Funding Wallet API and it is not part of order execution.

## Three distinct ledgers

The system must keep three kinds of state separate.

1. **External account facts** describe where assets, positions, equity, and
   available margin actually are. Account owns these facts and obtains live
   truth only through Account-owned Integration snapshot and event
   capabilities.
2. **Logical capital allocation** describes how much capital a strategy may
   use. Risk owns policies, budgets, and reservations. The target live
   topology gives each Strategy instance exclusive leases over every account
   in its capital group; Risk limits still apply within that isolation.
3. **Capital-operation lifecycle** describes an intended change in physical
   placement, such as an internal account transfer, Earn subscription, or Earn
   redemption. The authoritative Rust Capital application and Actor owned by
   the Strategy instance own these durable intents and their reconciliation;
   Integration only executes participant primitives.

Never infer successful settlement from an acknowledged command. A transfer or
redemption is complete only after its participant status is terminal and the
expected Account facts have been observed.

For a transfer operation, Capital persists `Prepared` before calling
Integration. For a participant operation without a provider idempotency key,
Capital then persists a delivery fence before entering the external call;
recovery queries a fenced operation and never submits it again. A confirmed submission advances only to participant
reconciliation; an indeterminate submission retains the same operation,
idempotency key, and source reservation. Participant success advances to
Account reconciliation, not completion. Completion requires newer complete
Account watermarks that show both the expected source debit and destination
credit. A definite participant rejection/failure releases the reservation;
delivery ambiguity never does.

## Ownership

| Concern | Owner |
|---|---|
| Balances, positions, equity, available margin, Earn positions | Account |
| Strategy/account limits and temporary capital reservations | Risk |
| Dynamic desired funding objective and its lifetime | Strategy |
| Static membership, route, product, and hard-limit configuration | Launch/control configuration |
| Effective liquidity targets, Earn deployment, and movement inside one Strategy capital group | Capital |
| Participant transfer, subscribe, redeem, and status-query calls | Integration |
| Exchange-facing order lifecycle | Execution |
| Target position or trade intent | Strategy |
| Risk shortfall observation and post-funding re-evaluation | Strategy instance composition |

Capital becomes a business module when the first end-to-end capital
allocation use case is implemented. Until then, do not put its state in
Integration, Account, Risk, or Execution merely to avoid creating the proper
owner.

## Runtime scope

Capital is an optional Strategy-instance capability. A Strategy can run
without Capital when its accounts are pre-funded or externally funded; Risk
and Execution must never require Capital health for an order whose required
funds are already Account-observed and available. When Capital is enabled,
that Strategy runtime instance owns exactly one Capital runtime instance. All accounts
exclusively assigned to the Strategy instance form one `CapitalGroup`,
including trading, funding, yield, and liquidity-buffer locations. The group
is the complete capital and liquidity boundary for that Strategy across
accounts, venues, and segments. There is no global Capital runtime, global
capital pool, or central Treasury allocation layer.

The relationship is one-to-one: a Capital runtime cannot manage several
groups, and a group cannot be attached to several live Strategy instances.
`capital_group_id` is the durable identity of the Strategy's assigned account
set and is preserved across restarts. `instance_id` identifies only the
current process run. A restarted instance recovers the same group journal and
reconciles in-flight operations before writing.

With `capital.enabled = false`, no Capital process, journal, demand sink, or
transfer wait state is created. A physical funding shortfall returns a
structured unavailable-funding result to the Strategy instead of waiting for
an absent service. With Capital enabled but degraded, only new capital
operations are disabled; already funded orders continue through normal
Account, Portfolio, Risk, and Execution checks.

Each live group has exactly one mutable state owner: the Capital Actor inside
the owning Strategy launch. It uses that launch's fenced account authorities
and owns funding-objective observations, effective targets, Capital
reservations, and operation plans. Account continues to own actual balances
and positions. Paper and
backtest create an instance-local simulated Capital runtime with the same
group semantics.

## Application and process boundary

Capital has one business owner but two caller-facing layers:

- `crates/modules/capital` is the authoritative business module. Its Actor is
  the only mutable owner of funding objectives, effective targets,
  reservations, plans, command attempts, and recovery state.
- `crates/modules/capital/contract` is the minimal cross-process contract for
  commands, queries, snapshots, and events used outside the Rust process.
- `kairospy.application.capital` is the Strategy-side application facade. It
  publishes or cancels a `FundingObjective`, queries Capital availability and
  health, and maps `capital.enabled = false` to an explicit disabled outcome.

The Python facade is a client, not a second Capital implementation. It must
not calculate authoritative effective targets, select transfer routes, call
Binance or another participant, persist transfer sagas, or infer settlement.
The Rust application must expose business request/result types rather than
Integration clients or vendor payloads. Cross-process snapshots and events
use contract-owned FlatBuffers types; JSON is limited to an explicitly
declared control/configuration boundary.

The current projection contains the policy envelope, active and terminal
objectives/demands, source facts and watermarks, availability, routes, plans,
reservations, and participant operations. Capital publishes each durable
transition as one typed event root on its own stream. The Actor outbox is
acknowledged only after publication succeeds, so a crash recovers the same
event sequence instead of manufacturing a new business transition.

Availability also retains deterministic `required_by` funding horizons. Each
horizon records its contributing objective and demand identities and their
net total-liquidity target. Overlapping observations in one horizon are
combined by maximum rather than summed, so duplicate or replacement trading
signals cannot manufacture capital demand.

Capital depends on Conflux, never on `kairos-integration`. Conflux owns the
participant-neutral transfer and liquid-yield rail types as well as the
construction of concrete Binance or future provider connections. Its adapter
maps those Conflux-owned requests, outcomes, status facts, and errors to
Integration types internally; Integration re-exports must not leak through the
rail signature consumed by Capital. Capital composition supplies only
non-secret account metadata and receives a Conflux-owned dispatcher; no Capital
source file constructs or imports a provider connection or provider DTO.
The dispatcher implements the Conflux Capital rails directly; it does not gain
those business capabilities by implementing an Integration command trait.

In `paper` and `backtest`, Conflux selects an instance-local simulated rail
before loading credentials or constructing participant connections. That rail
translates each transfer or Earn action into an idempotent, explicitly
simulation-only Account contract command. Account remains the sole owner of
balances and Earn holdings and rejects the command in live mode. Capital still
runs the same reservation, delivery fence, operation journal, reconciliation,
and settlement state machine; it neither calls the Account mutation endpoint
nor contains a separate paper business path.

Cross-Account connectivity is explicit account metadata, not a Capital-owned
global registry. `capital_controller_account_id` names the Account whose
credential controls a participant-side capital group, while
`participant_account_ref` identifies a non-controller member within that
group. For Binance the latter is the subaccount email; Capital never imports
or interprets that provider meaning. Two Accounts are automatically
transferable only when Conflux can prove they share the same configured
controller and provider environment. Unrelated independent Accounts remain
unsupported until a withdrawal/deposit rail with its own settlement policy is
implemented.

Launch includes the controller Account in the Strategy instance lease even
when a route only names two subaccounts. Immediately before a cross-Account
write, Capital revalidates both the source Account fence and the controller
Account fence. The source fence protects ownership of the funds; the
controller fence protects the credential that actually authorizes the write.

When Capital is disabled, Strategy composition constructs the disabled Python
facade without starting a Capital process. This preserves one Strategy API in
single-account and multi-account deployments while ensuring pre-funded order
execution has no synchronous Capital dependency.

A CapitalGroup is the business identity of a body of capital, not an account.
The model deliberately distinguishes two levels:

- `ExternalAccountIdentity` is an external account/security principal. A
  Binance master account, each Binance subaccount, and two independently
  registered Binance accounts are distinct Accounts.
- `AccountSegment` is a participant ledger inside one Account, such as Spot,
  Funding, USD-M Futures, COIN-M Futures, or Margin.

A balance location is identified by `Account + Segment + Asset`. The
CapitalGroup contains one or more Accounts and the permitted Segments within
them. Membership roles such as source, trading destination, liquidity buffer,
or yield location attach to these balance locations. The exclusive lease and
fencing unit is the entire Account, not an individual Segment. Spendable
capacity from one balance location may belong to only one live CapitalGroup at
a time; read-only observation may be shared.

## Account membership and transfer routes

Launch configuration resolves external accounts, credentials, environments,
and exclusive leases before constructing the Strategy instance. Capital does
not own credentials or duplicate Account state. It receives the resolved,
fenced member set and overlays group-local capital policy:

```text
Strategy instance configuration
        |
        +--> CapitalGroup membership and role
        +--> directed transfer-route configuration
        +--> current account lease/fencing owner
```

The Strategy configuration names its `capital_group_id`, member Accounts, and
permitted Segments.
No separate global group registry is required. Instance composition must
reject startup if another live Strategy instance holds a spendable lease for
any member location.

Movement between Segments of one Account and movement between Accounts in one
Strategy group are both rebalancing. Movement between two Strategy groups
changes ownership and is unsupported by the first system design; it cannot be
inferred from a route, demand, or Intent.

A transfer route is directed and explicit. Its configuration identifies:

- source and destination `Account + Segment`;
- participant operation kind, such as internal book transfer,
  account-to-account transfer, or withdrawal/deposit workflow;
- supported assets and optional amount/daily limits;
- required source authority and destination eligibility;
- expected settlement class and reconciliation query;
- enabled/disabled state and policy version.

Possessing an account lease is necessary but not sufficient to move funds.
Every debit operation also requires the active CapitalGroup Actor fencing token, an
enabled route, an authorized Capital rebalance decision, sufficient
unreserved balance, and applicable Risk/Capital limits. A Strategy lease authorizes its Execution
to make permitted writes inside that Strategy account; it does not authorize
arbitrary cross-account transfers.

The Capital Actor revalidates group membership; a matching
`capital_group_id` alone is not authority. Objective, policy, facts, route,
plan, and reservation locations must all reference configured Account and
Segment members. Recovered state is accepted only under the same versioned
membership configuration. A route authorization atomically reserves both
unreflected source balance and the still-unfilled destination deficit before
an Integration command can be emitted, preventing concurrent plans from
double-spending or overfunding the same target.

For a transfer between distinct Accounts, authority is checked on the source
side. Both Accounts and their endpoint Segments must be current members of the
same CapitalGroup, the source Account lease must belong to the owning Strategy
instance, and Account must observe the debit and credit before settlement
completes. A route may not be used to escape the group boundary.

Capital management must include, at minimum:

- versioned Strategy funding objectives, effective targets, reservations,
  liquidity buffers, and expiry;
- route allowlists, per-operation/daily limits, and circuit breakers;
- idempotent transfer plans and indeterminate-delivery reconciliation;
- Account-observed settlement and stale-fact rejection;
- fenced writer takeover, immutable audit correlation, and alerts;
- explicit return/recovery policy when a Strategy stops or a plan fails.

## Account dependency and readiness

Capital depends on Account and Portfolio application/contract facts; it never owns a
second balance feed. The owning launch composition starts one Account runtime
for each group member, builds the Portfolio view, and starts Capital only when
enabled.
Account uses read authority; the launch's fenced write authorities remain
available to Execution and Capital for their permitted operations.

Capital may be constructed or recover before Account becomes ready. It enters
`WaitingForAccounts` and becomes `Ready` only after all required member views
are complete, fresh, identity-matched, and at acceptable watermarks. Missing
optional members produce `Degraded`; missing critical members block new
writes.

Launch config assigns each member a `critical` or `optional` readiness role;
the safe default is `critical`. Capital observes Account-current metadata for
every member independently of whether that Account currently appears in a
policy or route. This liveness evidence is intentionally not recovered from
the Capital snapshot: after restart, the write barrier stays closed until the
current Account views have been observed again. An unavailable optional member
marks the group `Degraded` and freezes routes touching it, while unrelated
ready routes may continue. An unavailable critical member closes the global
new-operation barrier.

If Account becomes stale or unavailable:

- Capital freezes new plans that debit or credit the affected location;
- an acknowledged transfer remains `AwaitingAccountObservation` and is not
  retried merely because the balance view is absent;
- participant operation status may aid reconciliation, but cannot prove
  balance settlement;
- persisted snapshots may explain recovery state, but stale snapshots cannot
  authorize new capacity or close a plan.

Strategy instance composition starts Account dependencies before opening the
Capital write barrier and supervises them together. Capital application code
does not spawn Account processes. If an Account runtime stops later, Capital
degrades and freezes affected routes. Paper/backtest use the same
instance-local lifecycle.

Startup acquires the complete member lease set before enabling writes. A
partial lease or a critical Account readiness failure keeps Capital and
Execution unavailable and releases any newly acquired leases; the Strategy
must not start with a silently smaller capital group. Shutdown first closes
new order admission, then drains or reconciles active Execution and Capital
commitments, persists both owners, and finally releases member leases.

Capital shutdown has an explicit no-automatic-return policy. It closes new
objective/demand admission and automatic plan creation first, then spends a
bounded interval querying only participant operations that have already
crossed their durable delivery fence. It never creates the next operation,
resubmits, or issues a reverse transfer during shutdown. Any delivered effect
that is still awaiting participant or Account evidence retains its
reservation and is durably marked `ReconcileOriginalOperation`; a restarted
instance must continue with the same operation and idempotency key.

A definite pre-delivery expiry or participant rejection records
`NoCompensationRequired`. A participant-reported terminal failure records
`HoldAndReview`; Capital does not guess that an inverse movement is safe.
Operators may invoke `/v1/plans/reconcile` to query an existing fenced
operation, but that command rejects `Prepared` operations and contains no
amount, route, or idempotency-key override. Open `ReconcileOriginalOperation`
and `HoldAndReview` decisions are projected as warning or critical Capital
alerts with the plan, operation, reason, and durable decision timestamp. The
alert closes after Account-observed settlement; the transition remains in the
journal and typed event audit.

## Risk capacity and no-double-spend rules

Risk capacity has three independent inputs:

```text
configured policy limit
  - observed usage at an Account/Market watermark
  - active Risk reservations not yet reflected in observed usage
  = available Risk capacity
```

Risk is fundamentally the risk-budget ledger and admission authority. It maps
a proposed Execution plan into required budget usage, atomically reserves that
usage, and later consumes, resizes, expires, or releases the reservation.
Portfolio records current and historical portfolio facts; it does not grant
risk capacity.

Risk understands trade economics, not execution mechanics. Execution supplies
a normalized proposed action containing the Account/Segment, instrument,
side, quantity, bounded price, order exposure effect, and reduce-only intent.
Risk combines that proposal with Reference product facts, Account/Portfolio
state, and Market valuation to calculate incremental exposure, margin, stress,
concentration, and applicable budget usage. Execution must not supply an
authoritative pre-approved risk amount.

Risk does not select participant endpoints, order routes, slicing algorithms,
maker/taker tactics, retry behavior, remote order identifiers, or fill
reconciliation. Those remain Execution concerns. Provider-specific wallet or
order vocabulary also remains outside Risk.

An Account balance increase changes physical availability; it does not
automatically increase a Strategy policy limit. If Capital only fulfills an
already approved allocation, Risk keeps the same limit and refreshes its
Account facts. A true budget increase is an explicit, versioned Risk policy
command. A budget decrease must be rejected while existing usage
and reservations exceed the proposed limit.

Risk is a required, launch-scoped enforcement process for every order-producing
Strategy. It is not embedded in user Strategy code and does not depend on
Capital. If Risk is unavailable, Execution rejects new risk-increasing orders;
cancel and explicitly governed emergency risk-reduction paths remain
available. Signal-only launches with Execution disabled may omit Risk.

Metrics do not share one accounting rule:

- point-in-time metrics such as exposure, margin, leverage, loss, and drawdown
  are recomputed from fresh Account/Market facts plus unreflected reservations;
- window metrics such as turnover and order rate accumulate consumed usage and
  expire it according to their window;
- a transfer changes account liquidity and Capital allocation facts, but does
  not count as trade notional or silently rewrite a Risk policy.

Order funding and sell capacity are protected before the external command is
sent. Execution creates a durable `OrderCommitment` under its single Actor
transition, then obtains the Risk reservation, then submits the order. At
minimum:

- a Spot buy commits quote amount at a bounded worst-case price plus fees;
- a Spot sell commits base quantity;
- a derivative opening order commits initial margin and exposure;
- a derivative reduce/close order commits closeable position quantity for the
  relevant instrument and position side;
- an unbounded market order is rejected unless a fresh quote and explicit
  slippage/fee cap produce a conservative bound.

The next concurrent order sees active commitments even when the participant
has not acknowledged or Account has not refreshed, preventing double spend
and double sell. `Unknown/Indeterminate` orders keep their commitment until
reconciliation establishes a terminal fact.

Account may later report funds locked by the same open order. Capacity logic
must not subtract both that observed lock and the local commitment. Each
commitment therefore needs an observation-handoff state: unreflected
commitments are subtracted locally; once a complete Account observation at a
newer watermark contains the correlated order/lock, the observed fact replaces
the local physical deduction while the order and Risk audit correlation remain
active. Fills resize commitments; cancel/reject/expiry release them only after
delivery certainty is resolved.

Capital uses the same principle at CapitalGroup scope. A Capital Actor atomically
reserves source funds before submitting a transfer, so two rebalance plans
cannot spend the same group balance. It releases or consumes that reservation
only through participant reconciliation plus Account-observed settlement.

## Funding targets and rebalance decisions

Funding has four distinct layers:

1. Launch/control configuration defines the static governance envelope:
   CapitalGroup membership, permitted Segments and products, routes, source
   authority, hard floors/caps, transfer limits, and circuit breakers.
2. Strategy dynamically publishes a desired `FundingObjective` describing how
   much liquidity it expects at a balance location and by when. It may revise,
   cancel, or let that objective expire.
3. Risk supplies the maximum permitted risk capacity. A funding objective or
   physical transfer never raises that budget.
4. Capital combines these inputs with Account facts and owns the resulting
   effective target, rebalance decision, reservation, and external operation.

A Strategy funding objective contains, at minimum:

```text
objective_id + version
destination Account + Segment + Asset
desired_available
required_by + expires_at
priority + confidence
strategy_decision_id
```

It never names a source Account, transfer route, Earn product, participant API,
or retry plan. Those are Capital decisions. Publishing an objective therefore
does not authorize a movement.

Schedule, market-session, and historical-peak forecasting remain Strategy-side
analytics. The Strategy API represents their output as a typed
`FundingForecastObservation` with source, observation time, deadline,
confidence, and evidence references, then deterministically converts it into a
versioned `FundingObjective`. Historical-peak forecasting uses the maximum
observed requirement plus an explicit safety buffer; it does not introduce a
generic optimizer. Capital intentionally does not persist the forecasting
algorithm or consume Strategy clocks and market calendars: it aggregates the
resulting objectives by location and horizon, and still selects every source,
route, product, and operation itself.

Capital evaluates each objective inside a versioned policy envelope. The
envelope may define `minimum < default target < maximum`, a stress buffer,
minimum movement amount, deficit dwell time, cooldown, and hysteresis. The
effective target cannot exceed either the policy maximum or Risk-permitted
capacity. Static configuration supplies safe defaults when Strategy has no
active objective.

For objectives that target the same balance location, `desired_available`
describes the total desired available balance rather than an additive claim.
Capital therefore uses the highest active versioned target for that location,
then applies the policy stress buffer and clamps the result to the policy
maximum and Risk capacity. It must not sum overlapping objectives and create
artificial demand. If Account facts are incomplete/stale, their watermarks
move backwards, or Risk capacity is below the configured minimum, the
location is degraded and exposes no actionable deficit.

`CapitalDemandObserved` from a failed/deferred Execution plan is additional
advisory evidence, not the only source of demand. Capital deduplicates,
expires, nets, and aggregates it with active Strategy funding objectives by
destination, asset, and time horizon. It creates a rebalance decision only
when the resulting effective-target deficit persists,
Account facts are fresh, an intra-group route is enabled, source surplus is
unreserved, and all policy limits permit the movement. Repeated or replaced
Intents must not be blindly summed.

## Control loop

```text
HOT PATH
Strategy intent -> Execution plan -> Risk admission
                                      |
                     +----------------+----------------+
                     |                                 |
                 sufficient                    physical shortfall
                     |                                 |
          Risk reservation + order          defer/expire old plan
                                                       |
                                             demand observation only

SLOW PATH
Strategy funding objectives + Execution demand observations
Account facts + Risk budgets + static liquidity policy
                              |
                              v
                    aggregate by account/asset/horizon
                              |
                              v
                  minimum / target / maximum evaluation
                              |
                       rebalance decision
                              |
                  redeem / transfer / reconcile
                              |
                              v
                    Account observes settlement
                              |
                              v
             Strategy or durable Intent re-evaluates
                              |
                              v
                 new Execution plan + fresh Risk check
```

Strategy may publish a prospective liquidity objective, but never supplies an
authoritative raw margin requirement. Execution supplies the concrete planned
exposure; Risk calculates and authorizes the margin. The
Strategy instance may report a physical shortfall to its Capital runtime, but
that observation never authorizes a transfer. Capital aggregates it
with policy targets and other demand before creating a rebalance decision.

The normal production path is pre-funded. Capital maintains account buffers
before order admission, so most intents never wait for a transfer. A
short-lived trading intent expires when funding is unavailable. A durable
target intent may remain deferred, but its old Execution plan, market inputs,
OrderCommitment, and Risk reservation do not wait for a transfer. After
Account observes funding, the target is evaluated again and receives a new
plan and fresh Risk authorization.

Execution must not submit orders against capital that is merely planned,
submitted, or participant-acknowledged. The Account observation used for the
Risk decision must prove that the required margin is available.

## Logical allocation versus physical movement

The target production model is an account network, not a fixed master-account
hierarchy:

```text
Account A / Funding ----intra-account route----> Account A / USD-M
Account A / Funding ----inter-account route----> Account B / Funding
every Account (all of its Segments) -> exactly one live Strategy instance
```

A route endpoint is an `ExternalAccountIdentity` plus one of its addressable
Segments. A directed route records which operation can move a specific asset
between two endpoints and which authority is required. Funding-to-USD-M is an
intra-Account route. Binance master/subaccount transfer is an inter-Account
route. Two independently registered accounts are also separate Accounts, but
may require a withdrawal/deposit workflow rather than an internal transfer.
No Capital domain invariant depends on a `master` or `subaccount` label.

The current lease implementation already fences writes at external-account
scope. Capital must use its Strategy instance's authority for every
source-side write, while every member account remains exclusively leased to
that instance. A
multi-venue strategy may bind several dedicated accounts, but another live
Strategy launch cannot trade through them at the same time. Two strategies
never share one margin book.

Consider Strategy A's 100,000 USDT distributed across its own accounts:

```text
Funding liquidity buffer           20,000
Spot trading-account target        30,000
USD-M trading-account target       20,000
Earn deployment                    30,000
```

All locations belong to the same Strategy instance, but keep distinct physical
balances. If the target trading location already has sufficient collateral,
Risk creates a logical reservation and no participant transfer occurs. If it
has a physical shortfall, Capital selects an eligible intra-group route,
moves funds, and waits for Account to observe settlement. Execution only plans
and submits an order after the destination balance is available.

Capital must hold the route-specific source authority before it moves funds.
The destination must be a current member with a current funding target tied to
the same fenced Strategy instance. Capital creates its own source-funds reservation
while movement is pending. It does not keep an order-level Risk reservation or
OrderCommitment alive across the transfer. Failure or expiry releases the
Capital reservation according to the plan policy.

The ownership boundary determines the operation owner:

| Operation | Owner |
|---|---|
| Funding, USD-M, or Earn placement inside the Strategy group | Capital |
| Earn deployment and redemption | Capital |
| Movement between member external account identities | Capital |
| Trade orders inside a Strategy account | Execution |
| Movement between Strategy groups | Unsupported; requires a future explicit ownership workflow |

## Integration primitives

Capital does not depend on `kairos-integration` directly or through re-exported
Integration DTOs. Its composition uses Conflux-owned typed rail requests,
outcomes, status facts, and errors; Conflux alone maps them to a concrete
Integration connection. This keeps participant construction, connection
lifecycle, provider credentials, and provider capability vocabulary outside
the Capital business package.

Behind that boundary, Integration exposes two separate axes:

- `AssetTransferCommand` submits a movement between two locations belonging
  to one participant. `AssetTransferStatusQuery` establishes participant
  state. Cross-participant movement is a withdrawal/deposit workflow and must
  not be disguised as an atomic transfer.
- `EarnProductQuery`, `EarnCommand`, and `EarnActionStatusQuery` expose
  participant yield-product facts and operations. Product-native vocabulary
  is preserved when providers do not share a stable taxonomy.

Every mutating request carries an `IdempotencyKey`. A confirmed
`CommandOutcome` means the participant acknowledged the request; it does not
mean balances have settled. Ambiguous delivery remains `Indeterminate` and
must be queried or reconciled rather than retried blindly.

## Semantic extension policy

New participant features are classified by economic effect before code is
added. Marketing category and API pathname do not determine the system owner.

| Economic effect | Stable axis | Examples |
|---|---|---|
| Move the same asset between locations | Asset transfer | Funding to USD-M, master to subaccount |
| Deploy principal and later redeem it | Earn | Simple Earn, compatible staking products |
| Conditional payoff or asset conversion | Structured investment | Dual Investment, Discount Buy |
| Create debt secured by collateral | Credit/liability | Margin borrow, Crypto Loan, Institutional Loan |
| Transform a staking receipt token | Participant-native until shared | BETH/WBETH wrap, SOL/BNSOL actions |
| Move assets across a custody/network boundary | Withdrawal/deposit | On-chain withdrawal and confirmations |

A Binance-only operation first receives typed Binance request/result models on
its concrete connection. It becomes a participant-neutral capability only
when a business owner needs it and the shared economic semantics are proven.
This lets Binance coverage grow continuously without turning Integration into
a universal string-parameter API.

The first Capital adapter may use Binance master/subaccount transfer because
it is a concrete account-to-account rail. The Capital target and plan still
use source/destination account identities and route evidence, not master/sub
domain fields. Cross-participant movement is modeled separately as a
withdrawal/deposit workflow because it has address, network, fee,
confirmation, and custody risks that an internal transfer does not.

## Earn as capital deployment

Earn is neither cash nor immediately available margin. Capital policy must
apply a liquidity classification and, when appropriate, a haircut based on:

- redemption notice or maturity;
- daily redemption quota;
- early-redemption loss;
- principal and reward settlement times;
- participant and product availability.

An Earn position can contribute to total capital while contributing zero to
immediately available trading margin. Only Account-observed available balance
can close a redemption-and-transfer plan.

Idle-cash deployment is a separate, deterministic placement decision. An
`EarnSubscription` route remains at one `Account + Segment + Asset` location
and names one explicitly permitted product. Capital computes deployable cash
as `observed available - active Capital reservations - effective liquidity
target`; the effective target already includes the configured stress buffer.
The amount is then capped by the route's per-operation and daily limits.

Before authorizing the subscription, Capital obtains a principal-specific
product preview through Conflux. The product must be eligible, immediately
redeemable with zero declared delay, and have enough known redemption quota.
Unknown quota blocks automation unless the route configuration explicitly
accepts that participant limitation. A funding objective or demand whose
`required_by` falls inside the route's demand guard also blocks deployment.
Participant success advances only to Account reconciliation. Completion
requires a newer complete Account observation showing both the liquid balance
debit and the selected Earn product's principal increase.

The participant product catalog must be reviewed before extending this common
surface. Binance-specific findings and the resulting boundary decisions are
recorded in
[the Binance capital-product map](../integrations/binance-capital-products.md).
In particular, structured investments and collateralized loans are not plain
Earn products even when a participant markets them next to Earn.

## First end-to-end implementation

The first Capital slice should implement one concrete workflow, for example:
redeem Binance Simple Earn in one member account, move USDT through a supported
route into another member account or its USD-M segment, and observe the
resulting Account balance. The movement is triggered by a persistent deficit
against an effective target derived from an active Strategy objective and the
configured policy envelope, not by one Intent. Binance may implement that first route with its master/subaccount API,
but the business model must not depend on the hierarchy. A later Strategy
evaluation creates a fresh Execution plan. That slice must persist:

- objective version, policy version, effective target, rebalance decision,
  aggregated demand, and optional causal Strategy references;
- idempotency keys for every participant operation;
- Risk reservation identity;
- submitted, indeterminate, terminal, and reconciled states;
- participant references and Account observation watermarks;
- failure and compensation decisions.

Only after this workflow exists should more general planning, optimization,
or cross-participant rebalancing abstractions be introduced.
