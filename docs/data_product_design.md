# Data Product Design

Status: Draft
Date: 2026-07-21

This document defines the product boundary for Kairos Data. It is intentionally
user-facing: it describes what users should understand, what extension work they
must provide, and which data operations remain private implementation details.

## 1. Core Principle

Data Product is not the data engineering workspace.

Data Product exposes only governed data contracts and consumable data artifacts.
It does not expose provider cache files, raw downloads, staging directories,
runtime journals, retry receipts, or storage layouts as concepts users must
understand.

The product boundary is:

```text
private provider/cache/transform operations
  -> governed DataSet Contract
  -> Historical Release and/or Live View
  -> Study, Strategy, Run consumption
```

A user should think in terms of:

- `dataset_id`
- schema and `primary_time`
- historical release identity
- live view identity
- quality or freshness status
- query, freeze, replay, and audit behavior

A user should not need to think in terms of:

- `source/provider=...`
- provider REST pages
- `payload.zip`
- `receipt.json`
- temporary staging CSV files
- canonical journal file names
- lake-relative storage paths
- connector retry implementation

Those files can exist for audit and recovery, but they are Data Layer internals.

## 2. Product Model

Kairos Data has one stable identity model and two delivery shapes.

```text
DataSet Contract
  identity: dataset_id
  semantics: what the data means
  time: primary_time and boundary policy
  schema: field names, types, and compatibility rules
  quality: historical quality profile
  freshness: live health profile

Historical Release
  immutable, content-addressed historical data artifact

Live View
  continuously updated, monitored live data subscription artifact
```

Historical and live data are unified by the DataSet Contract, not by using the
same file format or the same transport.

They must share:

- `dataset_id`
- compatible schema
- `primary_time`
- instrument/reference identity semantics
- point-in-time meaning
- consumer-facing field definitions

They may differ in:

- provider transport
- storage format
- update cadence
- retry and reconnect behavior
- health checks
- replay materialization

## 3. What Is User Visible

The first user-visible data artifact is a governed product artifact.

| Artifact | User Visible | Purpose |
|---|---:|---|
| DataSet Contract | yes | Defines stable data identity and semantics |
| Historical Release | yes | Freezes a historical data window for study/backtest/replay |
| Live View | yes | Proves a live data stream is configured and healthy |
| Source Cache | no | Stores raw provider payloads for retry/audit |
| Receipt | no | Records provider request details and pagination |
| Staging Data | no | Intermediate normalization/import material |
| Runtime Journal | no by default | Audit/replay backing for live runs |
| Storage Layout | no | Internal lake organization and reader dispatch |

Audit tools may reveal private files when diagnosing an incident, but normal
product commands should summarize them as lineage, coverage, freshness, and
artifact hashes.

## 4. Historical Data

Historical data answers:

```text
For this DataSet Contract, what immutable data release covers this historical
window, and is it good enough for the requested use?
```

Historical data is used by:

- Study
- Backtest
- Historical Simulation
- Offline Replay
- Frozen evidence bundles

The public artifact is a Historical Release:

```text
dataset_id
release_id
content_hash
contract_hash
schema_id
primary_time
coverage
quality_level
lineage summary
published_at
```

The private operation behind it may include:

```text
download provider payload
cache receipt
normalize provider rows
map symbols to instrument_id
adjust prices
write parquet/csv/jsonl
compute hash
run quality profile
register release
```

The user should not choose or consume those intermediate files directly.

## 5. Live Data

Live data answers:

```text
For this DataSet Contract, is there a healthy live stream that can feed a
shadow, paper, or live run right now?
```

Live data is used by:

- Shadow run
- Paper trading
- Live trading
- Runtime monitoring
- Live capture replay after the run

For most providers, the user should only provide:

- account or credential reference
- provider environment, such as `testnet`, `paper`, or `live`
- subscription intent, such as instruments and channels
- run mode, such as `shadow`, `paper`, or `live`

The system owns:

- provider connection lifecycle
- WebSocket or polling transport
- reconnect and heartbeat behavior
- symbol to `instrument_id` mapping
- provider payload normalization
- canonical event capture
- freshness checks
- channel backpressure checks
- drop/overflow/sequence diagnostics
- replay artifact registration
- fail-closed runtime gate

The public artifact is a Live View:

```text
dataset_id
live_view_id
contract_hash
connector_hash
primary_time
fields
freshness_status
event_source_contract
channel_contract
transport
channel_diagnostics
freshness_evidence
```

The Strategy and Run products consume only a healthy Live View. They do not
consume account credentials, provider objects, WebSocket sessions, or journal
paths.

## 6. Where Historical and Live Are Unified

Historical and live data meet at the DataSet Contract.

```text
DataSet Contract
  -> Historical Release for study/backtest
  -> Live View for shadow/paper/live
```

For example:

```text
dataset_id: market.quote.crypto.binance.btc-usdt
schema: market.quote.v1
primary_time: available_time
instrument identity: crypto:binance:spot:BTCUSDT

Historical Release:
  replayable quote history for a fixed window

Live View:
  current quote stream with freshness and channel diagnostics
```

The user should be able to ask the same product questions for both:

```bash
kairospy data describe --dataset market.quote.crypto.binance.btc-usdt
kairospy data doctor --dataset market.quote.crypto.binance.btc-usdt
```

The answer should distinguish historical readiness from live readiness:

```text
historical:
  selected_release: ds_...
  quality_level: Q3
  coverage: complete

live:
  selected_live_view: lv_...
  freshness_status: healthy
  channel_diagnostics: passed
```

## 7. User Extension Boundary

When users add new data, they should provide only product-level inputs.

### 7.1 Required Work

The user must define:

1. DataSet Contract
2. Historical source adapter, live source adapter, or both
3. Credential references, when the source requires authentication
4. Optional quality/freshness policy overrides

The user must not define:

- internal lake directories
- release IDs
- content hashes
- raw cache paths
- provider receipt formats
- runtime capture paths
- catalog JSON internals

### 7.2 Historical Source

A historical source adapter returns bounded records for a DataSet Contract.

Typical source kinds:

- local CSV or Parquet
- Python provider function
- built-in vendor connector
- external vendor connector

Expected user flow:

```bash
kairospy data define --contract data/contracts/us-equity-returns.yaml

kairospy data source add \
  --dataset market.returns.equity.us.1d \
  --kind file \
  --path vendor_exports/returns.csv

kairospy data publish \
  --dataset market.returns.equity.us.1d \
  --start 2020-01-01T00:00:00Z \
  --end 2026-01-01T00:00:00Z \
  --quality Q3
```

System responsibilities during publish:

```text
read source
validate schema
normalize time and identity
compute coverage
run quality profile
write governed storage
compute content hash
write manifest/lineage/quality
register immutable release
```

### 7.3 Live Source

A live source adapter produces records continuously for a DataSet Contract.

Typical source kinds:

- provider WebSocket
- broker market data session
- polling connector
- user-defined event source

Expected user flow:

```bash
kairospy account connect binance --environment testnet

kairospy data define --contract data/contracts/binance-btc-quote.yaml

kairospy data live enable \
  --dataset market.quote.crypto.binance.btc-usdt \
  --account binance-testnet \
  --instrument BTCUSDT \
  --channel quote

kairospy data live doctor \
  --dataset market.quote.crypto.binance.btc-usdt
```

System responsibilities during live enable/start:

```text
resolve credentials
create connector runtime
subscribe to provider channel
normalize events
maintain capture/replay evidence
update freshness manifest
monitor channel health
expose healthy Live View
```

## 8. Consumption Boundary

Study, Strategy, and Run must depend only on governed Data artifacts.

Allowed dependencies:

- DataSet Contract
- Historical Release
- Live View
- Study input snapshot
- Run manifest evidence

Forbidden dependencies:

- provider client
- connector implementation
- raw file path
- source cache
- staging data
- runtime journal path
- mutable local working file

This keeps research and runtime semantics stable:

```text
Study proves signal behavior against a Historical Release.
Strategy binds to the same DataSet Contract.
Backtest consumes a frozen Historical Release.
Paper/Live consumes a healthy Live View for the same DataSet Contract.
Run artifacts record the exact release or live view evidence.
Replay verifies behavior from captured governed artifacts.
```

## 9. Quality and Freshness

Historical data uses quality gates.

Examples:

- schema compatibility
- required fields
- non-empty data
- primary key uniqueness
- point-in-time order
- coverage
- missing ranges
- reference identity coverage
- corporate action completeness
- feature no-future-data checks

Live data uses freshness and channel gates.

Examples:

- freshness max age
- connection status
- event count
- dropped messages
- channel overflow
- sequence gaps
- reconnect rate
- stale subscription
- replay capture availability

Quality and freshness have different checks, but both produce the same product
answer:

```text
Can this DataSet be consumed for the requested run mode?
```

Suggested minimum gates:

| Run Mode | Required Data Artifact |
|---|---|
| Study | Historical Release Q2+ |
| Backtest | Historical Release Q3+ |
| Historical Simulation | Historical Release Q3+ |
| Shadow | Historical Release Q3+ or healthy Live View |
| Paper | healthy Live View with replay capture |
| Live | production-approved healthy Live View with runtime evidence |

## 10. CLI Shape Target

The CLI should reflect product boundaries.

Suggested commands:

```text
data define                 register or update a DataSet Contract
data source add/list/remove manage historical or live source adapters
data publish                create a Historical Release
data live enable/start/stop manage Live Views
data list                   list DataSet products
data describe               describe contract, releases, and live views
data doctor                 report next action for historical and live readiness
data query                  query a Historical Release
data freeze                 freeze Data inputs for Study
data replay                 replay Historical Release or captured Live View
```

Existing commands can be mapped into this model:

| Current Command | Target Meaning |
|---|---|
| `data plan` | historical publish plan |
| `data acquire` | historical source acquisition plus release publish |
| `data prepare` | plan/acquire/validate/promote workflow |
| `data validate` | historical quality assessment |
| `data promote` | release approval transition |
| `data live-binance` | provider-specific live source smoke |
| `data soak-binance` | provider-specific live freshness evidence |
| `data write --live` | live view registration |
| `data write --file` | historical source import |

The product should gradually make provider-specific commands implementation
shortcuts rather than primary user concepts.

## 11. Design Decisions

1. DataSet Contract is the single user-facing identity boundary.
2. Historical Release and Live View are delivery shapes under that identity.
3. Source cache, receipts, staging, and runtime journals are private by default.
4. Users extend Data by declaring contracts and adapters, not storage paths.
5. Study, Strategy, and Run consume governed artifacts only.
6. Live users provide credentials and subscription intent; the system owns
   transport, capture, freshness, and replay evidence.
7. Quality and freshness are separate checks but share the same decision:
   whether a DataSet can be used for a run mode.

## 12. Open Questions

- Should `data download` remain a user-facing concept, or should it become an
  alias for `data publish` using a registered source?
- Should provider-specific live commands be hidden behind `data live enable`,
  or kept as diagnostic tools?
- What is the minimum evidence required before a Live View can be considered
  production-approved?
- Should `DataCatalog` store Live View manifests directly, or should Live View
  discovery remain path-based under `live-views/`?
- How much of source cache lineage should `data describe` summarize without
  exposing private paths?
