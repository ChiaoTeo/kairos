# Kairos Reference

Reference owns the workspace-wide reference universe: entities, venues,
assets, instruments, listings, markets, financial products, and their
lifecycle facts.

## Synchronization rule

Reference synchronization is full-universe synchronization. A provider
adapter must return every reference record available for its configured
provider/product. A concrete underlying such as `SPY` or `NVDA` must never be
a Reference-wide synchronization filter.

An instrument may still contain `underlying_instrument_id`; that is a domain
relationship describing the instrument, not a provider query option. Consumers
may filter query results by underlying after the full catalog has been built.

## Architecture

```text
bin -> composition -> application -> services -> domain
                         |             |
                    Integration      SQLite/Aeron
```

`ReferenceApplication` is the public use-case facade. `ReferenceActor` is the
single owner of mutable catalog state. Composition selects provider, storage,
and publication implementations. The server owns transport and process
lifecycle; it is not a second catalog state owner.

## Main capabilities

- provider-neutral catalog reconciliation;
- lifecycle events for listed, changed, and delisted markets;
- append-only lifecycle history with sequence/time filtering and replay;
- SQLite catalog recovery;
- market resolution and typed reference queries;
- catalog, markets, lifecycle, and change-event publication;
- one-shot CLI and workspace-managed Unix-socket server.

Provider-specific implementation details and current delivery status are
tracked in [`docs/reference-capabilities.md`](../../docs/reference-capabilities.md).

## Verification

```text
cargo test -p kairos-reference -p kairos-integration
cargo fmt --all -- --check
```
