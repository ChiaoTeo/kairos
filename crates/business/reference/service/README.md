# Kairos Reference

Reference owns the workspace-wide reference universe: entities, exchanges,
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

## Default startup

Reference owns its provider/product source registry. The registry is configured
in the Workspace manifest; the process does not select providers from command
line arguments:

```text
kairos-reference-server --workspace <workspace>
```

Configure providers in `<workspace>/kairos.toml` (or the discovered
`.kairos/kairos.toml`):

```toml
[reference.providers.massive]
enabled = true
credential_id = "massive-readonly"
endpoint = "https://api.massiveprivateserver.site"

[reference.providers.okx]
enabled = false
```

Transport and runtime options use the canonical names below:

```text
--aeron-channel <URI>
--reference-changes-stream <ID>
--refresh-interval <30s|5m|1h>
--snapshot-slot-size-mib <MiB>
--run-mode <daemon|once>
```

The Reference changes stream is a registered transport resource
(`kairos-transport::stream_ids::REFERENCE_CHANGES`, currently `1201`) and is
shared by Reference publishers and Market subscribers. The process rejects a
different stream ID; changing it requires changing the transport registry and
all consumers together.
Credentials are selected by Workspace credential ID and are never passed as
secrets on the command line.

Binance, OKX, and Hyperliquid public products are built in. Credentialed sources such as Massive are
added only when enabled under `[reference.providers.*]`; their credentials are
resolved from the Workspace credential store. Reference does not read
`market.connections`; that section belongs to the Market runtime.

Public sources can be disabled explicitly when a workspace does not want them:

```toml
[reference.providers.okx]
enabled = false

[reference.providers.hyperliquid]
enabled = false
```

Normal process control does not require a provider flag. The default registry
skips credential files with no resolved API key, includes credentialed sources
when their namespaced or conventional environment secret is available, and
reports the resulting source mode through `reference providers`. Use
`reference refresh` to request a refresh through the running process.

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
tracked in [`docs/reference-capabilities.md`](../../../../docs/reference-capabilities.md).

## Verification

```text
cargo test -p kairos-reference-service -p kairos-integration
cargo fmt --all -- --check
```
