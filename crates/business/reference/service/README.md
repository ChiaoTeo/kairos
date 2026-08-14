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

Binance Stocks Trading reference discovery is API-key protected and opt-in:

```toml
[reference.products.binance.equity]
enabled = true
credential_id = "binance-equity-readonly"
endpoint = "https://api.binance.com"
```

This enables only the verified Equity catalog endpoint. It does not enable or imply Binance Equity
quote or order APIs.

After the Reference process is running, the three Binance stock-related shapes
can be inspected independently:

```text
# Tokenized stock traded on Crypto Spot
uv run kairospy reference markets --exchange binance --symbol AAPLBUSDT --workspace . --format json

# Equity-underlying USD-M perpetual; the canonical instrument is perpetual and has no expiry
uv run kairospy reference markets --exchange binance --symbol AAPLUSDT --workspace . --format json

# Real US equity exposed through Binance Stocks Trading
uv run kairospy reference markets --exchange binance --symbol AAPL --workspace . --format json
uv run kairospy reference execution-accesses --provider binance --product-family equity \
  --provider-symbol AAPL --active-only --workspace . --format json
```

The Stocks Trading endpoint does not return the primary listing venue. Reference
therefore identifies the canonical instrument as a US equity and records Binance
as the execution provider without asserting that every returned symbol is listed
on Nasdaq.

If no Aeron consumer is running, refresh remains committed in SQLite. Durable
lifecycle rows and the publication cursor allow the publisher to resume without
copying every event payload into a second outbox table. Consumers recover their
bounded projections from the read-only SQLite contract.

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
tracked in the Reference service code, tests, and migration notes under
`docs/integration-migration-status.md`.

## Verification

```text
cargo test -p kairos-reference-service -p kairos-integration
cargo fmt --all -- --check
```
