# Risk target architecture

Risk is the authoritative pre-trade admission and reservation ledger. It does
not own balances, positions, market data, provider connections, or order
lifecycle. Those facts are supplied by Account, Market, Reference, and
Execution through composition-owned projections.

## State owner

`RiskActor` is the sole mutable state owner. Its command API has one
authoritative trade path:

```text
authorize_and_reserve(request) -> decision + reservation
```

Evaluation and mutation happen in one state-owner operation. The process uses
a bounded mailbox of 256 requests; it never uses an unbounded command queue.

Execution supplies an immutable `RiskContext` containing Account and Market
watermarks, Portfolio version, current exposure, margin availability, PnL, and
market freshness. Risk records the context on the decision for audit, but does
not own or mutate the Account, Market, or Portfolio facts.

## Policy semantics

Policies are versioned and immutable after activation. Matching policies are
hierarchical constraints: account, strategy, instrument, and exchange limits are
all enforced. A request is allowed only when every matching reject policy has
capacity. Each policy receives one allocation; a usage is never implicitly
charged multiple times to a synonym budget.

## Reservation lifecycle

```text
Reserved -> Consumed
         -> Released
         -> Expired
```

Transitions are idempotent and terminal states cannot transition again. Every
reservation has a TTL. The process periodically runs the expiration sweeper;
release, consume, and expire remain cleanup operations even when normal risk
dependencies are unavailable.

Circuit state is also Risk-owned. Opening and closing a circuit are journaled
and included in the Risk snapshot; an open matching circuit rejects new
authorizations while cleanup operations remain available.

## Durability

Mutations append a compact event to the local journal before acknowledging the
command. A full state snapshot is a periodic checkpoint only. Recovery loads
the checkpoint and replays journal entries after its event sequence. Snapshot
publication and event encoding are outside the decision calculation.

## Process and contract boundary

The UDS HTTP adapter is an input adapter only. It decodes a typed contract and
submits it to the bounded state loop. Cross-process models live in
`kairos-risk-contract`; domain and application APIs do not expose transport
types. Binary FlatBuffers schemas for the authorization request and decision
are in `schemas/risk/v1/authorize.fbs` and `schemas/risk/v1/decision.fbs`.

## Failure policy

Trade authorization fails closed for invalid, stale, incomplete, or
non-persistable state. Cleanup commands are allowed to proceed so a failure
cannot permanently strand reservations.
