# Capital management boundary

Document type: current architecture.

Capital management turns strategy demand into safe, reconciled capital
placement. It is not an alias for a participant's Funding Wallet API and it is
not part of order execution.

## Three distinct ledgers

The system must keep three kinds of state separate.

1. **External account facts** describe where assets, positions, equity, and
   available margin actually are. Account owns these facts and obtains live
   truth only through Account-owned Integration snapshot and event
   capabilities.
2. **Logical capital allocation** describes how much capital a strategy may
   use. Risk owns policies, budgets, and reservations. The target live
   topology gives Treasury authority over configured capital-source accounts
   and gives each Strategy launch an exclusive trading lease over its trading
   accounts; Risk limits still apply within that isolation.
3. **Capital-operation lifecycle** describes an intended change in physical
   placement, such as an internal account transfer, Earn subscription, or Earn
   redemption. A future Capital/Treasury application owns these durable
   intents and their reconciliation; Integration only executes participant
   primitives.

Never infer successful settlement from an acknowledged command. A transfer or
redemption is complete only after its participant status is terminal and the
expected Account facts have been observed.

## Ownership

| Concern | Owner |
|---|---|
| Balances, positions, equity, available margin, Earn positions | Account |
| Strategy/account limits and temporary capital reservations | Risk |
| Capital-source liquidity, central Earn, and account-to-account allocation/return | Capital/Treasury |
| Participant transfer, subscribe, redeem, and status-query calls | Integration |
| Strategy-account internal placement and exchange-facing order lifecycle | Execution |
| Target position or trade intent | Strategy |
| Risk shortfall to Treasury and Execution resume orchestration | System/launch composition |

Capital/Treasury becomes a business module when the first end-to-end capital
allocation use case is implemented. Until then, do not put its state in
Integration, Account, Risk, or Execution merely to avoid creating the proper
owner.

## Runtime scope

A live capital pool is a Workspace-scoped business resource because multiple
Strategy launches may request allocations from it. It is not owned by any one
Strategy instance. Workspace configuration identifies one or more pools by a
stable `capital_pool_id`, environment, managed accounts, assets, and permitted
routes.

Each pool still has exactly one mutable state owner: one active Capital Actor
instance holds a Workspace-scoped fenced writer lease for that
`capital_pool_id`. A standby or restarted process may take over only with a
new fencing token and after recovering the pool journal. The Actor owns
allocation requests, reservations, targets, and operation plans; Account
continues to own actual balances and positions.

There is no mandatory single global pool. Separate legal entities,
environments, custody domains, or operating mandates may require separate
pools within one Workspace. Live pools are normally supervised as shared
Workspace services. Paper and backtest pools are instance-scoped simulations
so concurrent runs remain deterministic and isolated.

## Account catalog, pools, and transfer routes

Workspace owns one global catalog of configured external accounts,
credentials, environments, and live leases. Capital does not copy or own that
catalog. A Capital pool selects account locations from it and overlays capital
policy:

```text
Workspace account catalog
        |
        +--> capital pool membership
        +--> directed transfer-route configuration
        +--> current account lease/fencing owner
```

A Workspace may configure a default pool named `global`, but code must still
address it by `capital_pool_id`; this avoids mixing live/testnet, legal
entities, or unrelated custody domains later.

A transfer route is directed and explicit. Its configuration identifies:

- source and destination account/segment;
- participant operation kind, such as internal book transfer,
  account-to-account transfer, or withdrawal/deposit workflow;
- supported assets and optional amount/daily limits;
- required source authority and destination eligibility;
- expected settlement class and reconciliation query;
- enabled/disabled state and policy version.

Possessing an account lease is necessary but not sufficient to move funds.
Every debit operation also requires the active pool Actor fencing token, an
enabled route, an authorized Capital request, sufficient unreserved balance,
and applicable Risk/Capital limits. A Strategy lease authorizes its Execution
to make permitted writes inside that Strategy account; it does not authorize
arbitrary cross-account transfers.

For a transfer between distinct accounts, authority is checked on the source
side. If the source is currently leased to a Strategy launch, an outgoing
return requires a current request/delegation from that lease owner; Treasury
cannot forcibly sweep it. The destination does not surrender its trading
lease, but the transfer must correspond to a current allocation request and
Account must observe the credit before settlement completes.

Capital management must include, at minimum:

- allocation targets, reservations, liquidity buffers, and expiry;
- route allowlists, per-operation/daily limits, and circuit breakers;
- idempotent transfer plans and indeterminate-delivery reconciliation;
- Account-observed settlement and stale-fact rejection;
- fenced writer takeover, immutable audit correlation, and alerts;
- explicit return/recovery policy when a Strategy stops or a plan fails.

## Risk capacity and no-double-spend rules

Risk capacity has three independent inputs:

```text
configured policy limit
  - observed usage at an Account/Market watermark
  - active Risk reservations not yet reflected in observed usage
  = available Risk capacity
```

An Account balance increase changes physical availability; it does not
automatically increase a Strategy policy limit. If Capital only fulfills an
already approved allocation, Risk keeps the same limit and refreshes its
Account facts. A true budget increase is an explicit, versioned policy or
allocation command. A budget decrease must be rejected while existing usage
and reservations exceed the proposed limit.

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

Capital uses the same principle at pool scope. A Capital Actor atomically
reserves source funds before submitting a transfer, so two allocation plans
cannot spend the same pool balance. It releases or consumes that reservation
only through participant reconciliation plus Account-observed settlement.

## Control loop

```text
Strategy trade/position intent
        |
        v
Execution route and exposure plan
        |
        v
Risk authorization and reservation
        +--> sufficient: Execution may submit
        |
        +--> insufficient: system creates Treasury allocation target
                              |
                              v
                         Capital movement plan
                              |
                              +--> redeem Earn if required
                              +--> account transfer if required
                              |
                              v
                     Participant status reconciliation
                              |
                              v
                   Account observes available margin
                              |
                              v
                      Risk re-authorization
                              |
                              v
                        Execution resumes
```

Strategy never supplies an authoritative raw margin requirement. Execution
supplies the planned exposure; Risk calculates and authorizes the margin. The
system routes a physical shortfall to the configured Workspace capital pool;
that pool creates the Treasury allocation target.

Execution must not submit orders against capital that is merely planned,
submitted, or participant-acknowledged. The Account observation used for the
Risk decision must prove that the required margin is available.

## Logical allocation versus physical movement

The target production model is an account network, not a fixed master-account
hierarchy:

```text
capital-source account(s) --transfer route--> Strategy trading account(s)
Strategy trading account -> at most one active live Strategy launch
```

An account node is an `ExternalAccountIdentity` plus one of its addressable
segments. A directed route records which operation can move a specific asset
between two nodes and which authority is required. Binance master/subaccount
transfer is one concrete route. Two independently configured accounts at the
same participant are another possible route. No Capital domain invariant
depends on a `master` or `subaccount` label.

The current lease implementation already fences writes at external-account
scope. Treasury must have authority for every source-side write, while every
Strategy trading account remains exclusively leased to its live launch. A
multi-venue strategy may bind several dedicated accounts, but another live
Strategy launch cannot trade through them at the same time. Two strategies
never share one margin book.

Consider 100,000 USDT distributed across configured capital accounts:

```text
Treasury liquidity buffer          20,000
Strategy A trading-account target  30,000
Strategy B trading-account target  20,000
Central Earn deployment            30,000
```

Each strategy is isolated in its own trading account even though Treasury can
allocate from a set of capital-source accounts. If Strategy A already has
sufficient collateral, Risk creates a logical reservation and no participant
transfer occurs. If it has a physical shortfall, Treasury selects an eligible
route, moves the allocation to Strategy A's account, and waits for Account to
observe settlement. Execution may then perform Funding-to-USD-M placement
inside Strategy A's account.

Treasury must hold or present the route-specific source authority before it
moves capital. A destination Strategy launch must provide a current allocation
request tied to its fenced account ownership; Treasury must not push funds
into an unrelated or stale launch. The logical reservation remains active
while movement is pending so that another intent from the same strategy
cannot consume the same allocation. Failure or expiry releases the
reservation according to the Capital plan's policy.

The ownership boundary determines the operation owner:

| Operation | Owner |
|---|---|
| Funding, USD-M, or Earn placement inside one Strategy account | Execution |
| Earn deployment and redemption in a central capital account | Capital/Treasury |
| Movement between distinct external account identities | Capital/Treasury |
| Trade orders inside a Strategy account | Execution |

## Integration primitives

Integration exposes two separate axes:

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

The first Treasury adapter may use Binance master/subaccount transfer because
it is a concrete account-to-account rail. The Capital request and plan still
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

The participant product catalog must be reviewed before extending this common
surface. Binance-specific findings and the resulting boundary decisions are
recorded in
[the Binance capital-product map](../integrations/binance-capital-products.md).
In particular, structured investments and collateralized loans are not plain
Earn products even when a participant markets them next to Earn.

## First end-to-end implementation

The first Capital/Treasury slice should implement one concrete workflow, for
example: redeem Binance Simple Earn in a configured capital-source account,
move USDT through a supported Binance account-to-account route into a Strategy
account, let that Strategy's Execution place the funds into its USD-M segment,
observe the resulting Account balance, and then release the strategy to trade.
Binance may implement that first route with its master/subaccount API, but the
business model must not depend on the hierarchy. That slice must persist:

- capital plan and strategy correlation;
- idempotency keys for every participant operation;
- Risk reservation identity;
- submitted, indeterminate, terminal, and reconciled states;
- participant references and Account observation watermarks;
- failure and compensation decisions.

Only after this workflow exists should more general planning, optimization,
or cross-participant rebalancing abstractions be introduced.
