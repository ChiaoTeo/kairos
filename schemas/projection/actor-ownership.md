# Actor-owned projections

An Actor owns a projection when it is the only component allowed to mutate the
underlying state and publish a new generation. Consumers may read the snapshot
and subscribe to the Actor's event stream, but they do not rebuild or write the
shared state.

## ReferenceActor

ReferenceActor owns versioned definitions of the trading world:

| Root | Purpose |
| --- | --- |
Reference current-state projections are owned by the versioned read-only
SQLite contract; Reference FlatBuffer projections were retired.
| `reference/v1/lifecycle.fbs` | Recent lifecycle facts |

Reference data is effective-dated. A consumer must use the snapshot's `as_of`
and generation rather than assuming that the newest wall-clock record is valid
for every historical event.

## MarketActor

MarketActor owns the state produced by market observations and subscription
commands:

| Root | Purpose |
| --- | --- |
| `market/v1/current.fbs` | Latest quote, trade, bar, rate, and Greeks state |
| `market/v1/orderbook.fbs` | Current synchronized order books and depth |
| `market/v1/history.fbs` | Bounded warm-up history for strategies |
| `market/v1/subscriptions.fbs` | Subscription ownership, status, and freshness |

Current state and warm-up history are separate roots because they have
different update rates, sizes, and reader access patterns. Order books are
separate because a full depth update must not force every quote reader to map
or copy the book.

## Snapshot and event independence

A snapshot is a one-shot current-state query. An event subscription starts
from a cursor defined only by the event contract. Reading a snapshot cannot
choose, advance, validate, or recover that cursor; an event gap must be handled
by event retention/resync or reported as an error.

## Non-ownership

Strategy-local indicators, model state, and signal history belong to the
StrategyActor. They may be rebuilt from MarketActor history, but they are not
shared MarketActor projections. REST, UI, and monitoring processes are readers
of Actor-owned snapshots; they are not projection owners.

## AccountActor

AccountActor owns balances, positions, and account-level valuation:

| Root | Purpose |
| --- | --- |
| `account/v1/current.fbs` | Current state for all account scopes owned by the Actor |
| `account/v1/equity.fbs` | Bounded equity history for reporting and warm-up |

Orders, fills, and risk reservations are deliberately absent from the
AccountActor snapshot. They are separate Actor-owned facts.

## ExecutionActor

ExecutionActor owns the order and fill lifecycle:

| Root | Purpose |
| --- | --- |
| `execution/v1/orders.fbs` | Current order state and lifecycle counters |
| `execution/v1/fills.fbs` | Fill ledger projection |

ExecutionActor may consume risk decisions and account information, but it
does not become the owner of those states.

## RiskActor

RiskActor owns budgets and reservations:

| Root | Purpose |
| --- | --- |
| `risk/v1/budgets.fbs` | Current budget usage and reservation state |

An order or intent can reference a reservation, but the reservation remains
owned and updated by RiskActor.

## IntentActor

IntentActor owns the lifecycle of strategy intents after they have crossed the
strategy boundary:

| Root | Purpose |
| --- | --- |
| `intent/v1/journal.fbs` | Current intent states, ownership, and linked orders |

StrategyActor owns the decision and private model state that produced an
intent. IntentActor owns the accepted/rejected/active/completed lifecycle and
is the source for cross-strategy and operational queries.

## SystemMonitorActor

SystemMonitorActor owns operational observations, not business truth:

| Root | Purpose |
| --- | --- |
| `system/v1/health.fbs` | Actor and connection health |
| `system/v1/operations.fbs` | Cross-Actor operation chains |
| `system/v1/alerts.fbs` | Active and resolved operational alerts |
| `system/v1/freshness.fbs` | Event freshness by domain |

These snapshots are recoverable operational state. They do not replace the
business Actor that owns an order, account, market, intent, or risk decision.
