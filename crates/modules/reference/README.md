# Kairos Reference

Reference is Kairos's authority for canonical tradable identity, effective
reference relationships, and their lifecycle. It maps Integration-owned
provider facts into stable assets, instruments, listings, and markets.

Reference does not own provider networking, market observations, execution
routing policy, account facts, workspace resources, or research datasets.

## Synchronization rule

Reference synchronization covers the complete explicitly configured scope of
each provider/product. A source may use durable, explicit coverage (for
example selected option underlyings), but an ad-hoc consumer query must never
silently narrow authoritative synchronization.

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
```

`kairos-reference-server` is always a long-running daemon. One-shot refresh or
publication behavior belongs behind explicit maintenance/bootstrap commands,
not behind a server run mode.

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
```

The Stocks Trading endpoint does not return the primary listing venue. Reference
therefore identifies the canonical instrument as a US equity without asserting
that every returned symbol is listed on Nasdaq. Whether Kairos can observe or
execute it is queried from the running Market or Execution module, respectively.

If no Aeron consumer is running, refresh remains committed in SQLite. Every
lifecycle publication is encoded as typed FlatBuffers and stored in the same
transaction as its exact catalog revision. The publisher retries those bytes
until acknowledgement; it never reconstructs an old event from a newer current
row.

Current canonical state is read through the contract-owned, read-only SQLite
client with one transaction-consistent generation/event-sequence watermark.
Market and Execution combine those identities with their configured Integration
capabilities at runtime; Account receives only the identity facts needed to map
provider observations. Business modules never issue Reference SQL or depend on
its table names and persistence records.

Normal process control does not require a provider flag. The default registry
skips credential files with no resolved API key, includes credentialed sources
when their namespaced or conventional environment secret is available, and
reports the resulting source mode through `reference providers`. Use
`reference refresh` to request a refresh through the running process.

## Architecture

```text
bin -> composition -> application -> services
          |                 |             |
     Integration         domain         SQLite
          |
    typed publishers
```

`ReferenceApplication` is the public use-case facade. `ReferenceActor` is the
single owner of mutable catalog state. Composition selects concrete
Integration clients, storage, and publication implementations. The server
adapts process transport; it is not a second catalog state owner.

## Main capabilities

- provider-neutral catalog reconciliation;
- lifecycle events for listed, changed, and delisted markets;
- append-only lifecycle history with sequence/time filtering and replay;
- atomic SQLite catalog recovery and typed publication outbox;
- market resolution and typed reference queries;
- catalog, markets, lifecycle, and change-event publication;
- read-only/operator CLI backed by the workspace-managed Reference daemon.

Provider-specific implementation details are tracked in the Reference service
code, tests, and adapter provenance under
`docs/integrations/adapter-provenance/`.

## Query boundary

Reference owns the canonical catalog and the Rust `ReferenceActor` remains its
only mutable state owner. The Python query surface is read-only:
`ReferenceClient` owns knowledge of the SQLite projection, while
`ReferenceApplication` maps contract records into strategy-safe business
models. Strategy code and CLI commands do not import table names or issue
arbitrary SQL.

`ReferenceReadSession` holds one read-only SQLite transaction and pins its
catalog generation and event sequence. `ReferenceApplication.snapshot()`
scopes that session for a strategy decision so related entity, asset,
instrument, listing, market, execution-access, and market-data-access queries
cannot accidentally combine different catalog generations. One-shot queries
use the same indexed filters and typed results.

The CLI exposes that typed query inventory, including batch IDs, pagination,
market-data access and option-chain filters. It intentionally does not expose
arbitrary SQL because the table layout is a contract implementation detail.

Coverage includes catalogs larger than 10,000 markets, WAL-backed concurrent
read/write generations, batch lookup, pagination, option-chain filters,
not-found behavior, and SQLite query-plan checks for production indexes.

## Verification

```text
cargo test -p kairos-reference -p kairos-integration
make rust-fmt-check
```
