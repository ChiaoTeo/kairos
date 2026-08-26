# Account wire contract v2

Account v2 has three distinct contract families:

```text
events/  immutable Account-owned state transitions published on the stream
views/   per-entity LMDB current-value roots
types/   semantic value groups shared by Account event and current-value roots
```

`AccountFactProvenance` is an Account type because it explains an accepted
Account transition; it is not a copy of a Binance or IBKR payload. A table
belongs beside its event or view root unless it is a semantic type shared by
multiple Account roots.

Account v2 does not define provider-specific control messages as FlatBuffers
roots. Provider connection, refresh, reconciliation, and control semantics
remain outside this business wire contract. Account runtime/resource isolation
is defined by [`isolation.md`](./isolation.md). Runtime adoption is evidenced
by the owning module's contract, publication and architecture tests; generated
bindings alone do not prove that the running Account process publishes v2.
The indexed layout separates segments, balances, collateral, positions, valuations, earn holdings,
and observed orders by stable business key. The former aggregate roots and fallback decoders have
been removed.

## Account v2 surface

Events:

- `BalanceUpserted` / `BalanceRemoved`
- `PositionUpserted` / `PositionRemoved`
- `ValuationChanged`
- `AccountStatusChanged`
- `ObservedOrderUpserted` / `ObservedOrderRemoved`

Current values are the seven `*Current` entity roots registered for the named LMDB databases.

Semantic types:

- `Balance`
- `Position`
- `AccountValuation`
- `ObservedOrder`
- `AccountSegmentState`
- `AccountFactProvenance`

Each event is one immutable Account-owned transition for one `account_id` and
one `segment_key`. `metadata.event_id` identifies the transition and
`metadata.sequence` is the event-stream position. When several changes come
from one provider frame, their `AccountFactProvenance.provider_event_id`
values correlate them without introducing an omnibus Account event.

An event is an immutable transition fact. A current view is the latest merged
state image. View generations, segment state generations, provider event IDs,
and provider timestamps are evidence about the image; they are not event
stream cursors or replay positions.

## Provider boundary

Binance and IBKR do not publish the same account protocol. Binance provides
balance result updates, balance deltas, and order/execution events. IBKR
provides key/value account updates, portfolio position updates, completion
notifications, and separate open-order observations. Integration maps those
facts into Account-owned transitions:

```text
provider fact
  -> normalized Account change
      -> one Account event root
  -> merged Actor state
      -> owner-scoped indexed entity values
```

Provider partial updates are merged by the Actor before one atomic indexed diff is committed.

`ObservedOrder` is a provider observation of what is currently visible. It is
not an Execution order lifecycle and does not contain terminal order history
unless a future consumer explicitly defines that contract.

Optional Decimal fields use this rule:

```text
null = provider did not report the value or it is not applicable
zero = provider explicitly reported zero
```

`Balance` is used in both `balances` and `collateral`; the vector in which the
value appears supplies that Account-owned role. `asset_id` is the canonical
identity and `asset_code` is the provider/display code.
Account v2 schemas are split by account-owned fact family. Balance, position,
valuation, provider-observed order, provenance, and segment state types have
independent files under `types/`.

Account event roots are split by owned fact family. They publish normalized
changes, not provider payloads or current-view snapshots. Balance and position
events carry the resulting state; a provider delta is optional evidence and
must not be required to rebuild the Account current view.
