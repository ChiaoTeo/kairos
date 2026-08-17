# Kairos Conflux

Conflux is the closed, typed, single-writer runtime for long-running Kairos
modules.

Each process declares:

- exactly one concrete owning `Contract` that it serves;
- one system-supplied, statically typed universe of all dependency Contract
  clients;
- one system-supplied, statically typed universe of all Integration
  connections;
- one closed ingress type and one closed output type.

Conflux currently abstracts only a Contract's closed REST request/response
pair. View, Aeron, SQLite projections, transport construction, and other
capabilities remain on each module's concrete Contract implementation. See
`examples/contract_runtime.rs` for the smallest complete service.

The same concrete client or connection type may have multiple runtime-named
instances through `ManagedClients<K, C>` and `ManagedConnections<K, C>`.
Different resource types remain explicit fields in system-owned structs.
Actors do not declare resource subsets: they create and use instances from the
complete system universe on demand. Conflux does not use `TypeId`, `Any`,
downcasts, erased dispatch, or an open resource catalog.
