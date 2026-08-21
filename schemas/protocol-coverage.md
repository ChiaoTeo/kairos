# Domain to wire-protocol coverage

The Python and Rust domains are richer than the process-boundary protocol.
Only stable cross-process facts, commands, and published read models belong in
the active v2 schema set.

## Boundary rules

| Domain concept | v2 wire treatment |
| --- | --- |
| Market observations | Independent typed event roots |
| Reference identities and market definitions | Reference lifecycle events plus SQLite point-in-time queries |
| Trade intent and execution lifecycle | Execution commands and typed intent/order/fill events |
| Account balances, positions, equity, and observed orders | Account-owned events and current views |
| Risk authorization and reservations | Typed authorization, reservation, decision, and circuit roots |
| History, ledgers, journals, and audit records | Bounded query or dataset contracts |
| JSON-RPC control requests and responses | Contract-owned Rust RPC traits and JSON serialization tests |

## Current v2 roots

Published roots are listed in [`v2/registry.md`](v2/registry.md), grouped by
owner and contract shape. A root enters the active generation set only after
its owner, publisher or caller, consumer, transport profile, identity, bounds,
freshness policy, failure behavior, and Rust/Python mapping tests are named.

## Explicit exclusions

Provider payloads, mutable journals, actor runtime contexts, selectors, query
objects, and internal synchronization state are not stable wire contracts.
They remain behind adapters and are converted to typed v2 contracts at the
process boundary.
