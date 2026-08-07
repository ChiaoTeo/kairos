# Domain to wire-protocol coverage

The Python domain is intentionally richer than the process-boundary protocol.
Only stable cross-process facts, commands, and published read models belong in
`schemas/`; internal state and algorithms do not.

## Boundary rules

| Domain concept | Wire treatment |
| --- | --- |
| Market observations (`Quote`, `TradePrint`, `Bar`, order-book snapshot/delta, rates, greeks) | Independent event roots |
| Reference identities and market definitions | Independent reference messages/snapshots |
| `TradeIntent` | Independent command and lifecycle facts |
| `OrderRequest` / `OrderEvent` | Independent order command/event roots |
| `ExecutionUpdate` | Split into explicit execution facts; no dynamic metadata map |
| Account ledger facts, balances, positions, open orders | Account facts and published snapshots |
| Risk checks and reservations | `risk/v1/assess.fbs`, `reserve.fbs`, `release.fbs`, `consume.fbs`, `assessment_result.fbs`, `reservation_event.fbs`, plus the Risk snapshot |
| Windows, readers, journals, ledgers, synchronizers, projectors | Internal implementation |
| `AccountRuntimeContext` and composition resources | Internal implementation |
| REST control requests/responses | Control-surface DTOs, not market-data FBS |

## Actor-owned projection roots

Published snapshot roots are grouped by the Actor that owns and publishes the state.
The first read-only roots are:

```text
ReferenceActor:
  projection/reference/v1/catalog.fbs
  projection/reference/v1/markets.fbs
  projection/reference/v1/lifecycle.fbs

MarketActor:
  projection/market/v1/current.fbs
  projection/market/v1/orderbook.fbs
  projection/market/v1/history.fbs
  projection/market/v1/subscriptions.fbs

AccountActor:
  projection/account/v1/current.fbs
  projection/account/v1/equity.fbs

ExecutionActor:
  projection/execution/v1/orders.fbs
  projection/execution/v1/fills.fbs

RiskActor:
  projection/risk/v1/budgets.fbs

IntentActor:
  projection/intent/v1/journal.fbs

SystemMonitorActor:
  projection/system/v1/health.fbs
  projection/system/v1/operations.fbs
  projection/system/v1/alerts.fbs
  projection/system/v1/freshness.fbs
```

Each root is an immutable snapshot with a `SnapshotHeader` containing the
owning Actor identity, publication generation, event stream, and event
sequence watermark. The Actor creates a new buffer, publishes its version,
and never mutates a buffer held by a reader. This makes mmap/memfd/shared-
memory reads possible without building a second object graph. A new consumer
starts from the snapshot and then follows the owner Actor's event stream from
the recorded watermark.

Snapshot roots reuse the stable business types from their owning namespace.
They may contain read-oriented aggregate counters and bounded collections, but
must not introduce implementation-shaped `State`, `Directory`, or `Current`
types. Events optimize for append/replay; snapshots optimize for direct reads.

Account projections do not contain order reservations or execution ledger
state. Execution projections do not contain account balances or risk
reservations. Risk reservations belong to RiskActor and are correlated to
orders through intent/account/instrument identifiers.

## Explicit exclusions

`MarketObservation.payload: Mapping[str, object]`, provider payloads, view
readers, selector/query objects, mutable journals, and actor runtime contexts
are not stable wire contracts. They remain behind adapters and are converted
to typed messages or snapshots at the process boundary.
