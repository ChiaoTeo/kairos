# Kairos Reference

Reference is Kairos's authority for canonical tradable identity, effective
reference relationships, and their lifecycle. It maps Integration-owned
provider facts into stable assets, instruments, listings, and markets.

Reference does not own provider networking, market observations, execution
routing policy, account facts, workspace resources, or research datasets.

## Synchronization rule

Reference synchronization covers the complete configured scope of each
provider. An internal source may use durable, explicit coverage (for
example selected option underlyings), but an ad-hoc consumer query must never
silently narrow authoritative synchronization.

An instrument may still contain `underlying_instrument_id`; that is a domain
relationship describing the instrument, not a provider query option. Consumers
may filter query results by underlying after the full catalog has been built.

## Default startup

Reference owns its source registry. A workspace does not need a Reference
section to start: public Binance, OKX, and Hyperliquid catalogs run with their
built-in defaults, while credentialed providers remain disabled. The process
does not select providers from command-line arguments:

```text
kairos-reference-server --workspace <workspace>
```

The normal configuration surface is provider-level. For example, enable
Massive or disable a public provider in `<workspace>/kairos.toml` (or the
discovered `.kairos/kairos.toml`):

```toml
[reference.providers.massive]
enabled = true
credential_id = "massive-readonly"
endpoint = "https://api.massiveprivateserver.site"

[reference.providers.okx]
enabled = false
```

Runtime tuning is optional and nested separately from provider selection:

```toml
[reference.runtime]
refresh_interval_seconds = 60

[reference.runtime.tick_budget]
max_sources_per_tick = 3
max_batches_per_source = 5
```

Provider endpoint overrides are also advanced settings. They stay below the
provider and do not expose Reference source IDs or Integration product codes:

```toml
[reference.providers.binance.endpoints]
spot = "https://api.binance.com"
usd_m_futures = "https://fapi.binance.com"
```

Transport and command-line runtime overrides use the canonical names below:

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

Binance, OKX, and Hyperliquid public catalogs are built in. Credentialed
providers such as Massive are added only when enabled under
`[reference.providers.*]`; their credentials are resolved from the Workspace
credential store. Reference does not read `market.connections`; that section
belongs to the Market runtime.

Public sources can be disabled explicitly when a workspace does not want them:

```toml
[reference.providers.okx]
enabled = false

[reference.providers.hyperliquid]
enabled = false
```

Binance Stocks Trading reference discovery is API-key protected and opt-in.
Providing a Binance credential enables that catalog without asking the user to
select an internal product binding:

```toml
[reference.providers.binance]
credential_id = "binance-equity-readonly"

[reference.providers.binance.endpoints]
equity = "https://api.binance.com"
```

This enables only the verified Equity catalog endpoint. It does not enable or imply Binance Equity
quote or order APIs.

After the Reference process is running, the Binance stock-related shapes remain
independent:

```text
# Tokenized stock traded on Crypto Spot
uv run kairospy reference markets --exchange binance --symbol AAPLBUSDT --workspace . --format json

# Equity-underlying USD-M perpetual; the canonical instrument is perpetual and has no expiry
uv run kairospy reference markets --exchange binance --symbol AAPLUSDT --workspace . --format json

```

Real US equities such as `AAPL` exposed through Binance Stocks Trading appear in
Kairos I under the service-provider trading channels for that instrument. They
do not appear as markets whose exchange is Binance. The endpoint does not return
the primary listing venue, so Reference identifies the canonical US equity and
records Binance availability without asserting Nasdaq, NYSE, or another listing
exchange. Whether Kairos can observe or execute it is queried from the running
Market or Execution module, respectively.

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
runs public providers, adds explicitly enabled credentialed providers, and
reports detailed source state only through the advanced `reference providers`
control surface. Use `reference refresh` to request a refresh through the
running process.

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
- append-only lifecycle persistence for recovery and publication, without a public history query;
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
the Rust owner contract owns all knowledge of the SQLite catalog, while
`ReferenceApplication` maps contract records into strategy-safe business
models. Strategy code and CLI commands do not import table names or issue
arbitrary SQL.

The native `ReferenceReadSession` holds one read-only SQLite transaction and
pins its catalog generation and event sequence. Its Python facade contains no
connection, SQL, table name, or JSON row decoder. `ReferenceApplication.snapshot()`
scopes that session for a strategy decision so related exchange, asset,
instrument, listing, and market queries
cannot accidentally combine different catalog generations. One-shot queries
use the same indexed filters and typed results.

The CLI exposes that typed query inventory, including batch IDs, pagination,
asset-code resolution and option-chain filters. It intentionally does not expose
arbitrary SQL because the table layout is a contract implementation detail.

Coverage includes catalogs larger than 10,000 markets, WAL-backed concurrent
read/write generations, batch lookup, pagination, option-chain filters,
not-found behavior, and SQLite query-plan checks for production indexes.

## Verification

```text
cargo test -p kairos-reference -p kairos-integration
make rust-fmt-check
```
