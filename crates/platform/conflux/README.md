# Kairos Conflux

Conflux is the closed, typed, single-writer runtime for long-running Kairos
modules.

Each process declares:

- one Actor that directly implements its unique owning `Contract`;
- one system-supplied, statically typed universe of all dependency Contract
  clients;
- one system-supplied, statically typed universe of all Integration
  connections;
- one global `ConfluxEvent` handler and an optional local event type.

Conflux provides typed JSON-RPC control service registration for each
long-running module contract. View, Aeron, SQLite projections, transport
construction, and other capabilities remain on each module's concrete
Contract implementation.

`ConfluxHandle::handle(event)` is the only event entry point for actor-owned
work. JSON-RPC methods adapt process control requests into typed application
calls and keep transport framing out of module actors.

The same concrete client or connection type may have multiple runtime-named
instances through `ManagedClients<K, C>` and `ManagedConnections<K, C>`.
Different resource types remain explicit fields in system-owned structs.
Actors do not declare resource subsets: they create and use instances from the
complete system universe on demand. Conflux does not use `TypeId`, `Any`,
downcasts, erased dispatch, or an open resource catalog.

Shutdown has one absolute deadline covering queued-event drain and the Actor's
`stopping` hook. Exceeding it converts the outcome to `Forced` and aborts any
remaining supervised source tasks.
