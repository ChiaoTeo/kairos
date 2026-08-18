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

Conflux currently abstracts only a Contract's closed REST request/response
pair. View, Aeron, SQLite projections, transport construction, and other
capabilities remain on each module's concrete Contract implementation.

`ConfluxHandle::handle(event)` is the only event entry point. REST events
return `Some(response)` through the framework; all other events return `None`.

The same concrete client or connection type may have multiple runtime-named
instances through `ManagedClients<K, C>` and `ManagedConnections<K, C>`.
Different resource types remain explicit fields in system-owned structs.
Actors do not declare resource subsets: they create and use instances from the
complete system universe on demand. Conflux does not use `TypeId`, `Any`,
downcasts, erased dispatch, or an open resource catalog.

Shutdown has one absolute deadline covering queued-event drain and the Actor's
`stopping` hook. Exceeding it converts the outcome to `Forced` and aborts any
remaining supervised source tasks.
