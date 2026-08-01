# Application Ports and Domain Boundaries

This document defines how KairosPy should separate domain types, application ports, and infrastructure integrations. It complements `docs/integration-boundaries.md`, which explains how external participants are organized under `infrastructure/integrations`.

## Goal

Application services should depend on KairosPy business contracts, not on exchange SDK shapes. Infrastructure integrations should adapt external APIs into those contracts.

The intended dependency direction is:

```text
core
  <- application ports
  <- application services
  <- surface / runtime composition

infrastructure integrations
  -> core
  -> application ports

infrastructure persistence
  -> core
```

Arrows point from the importing layer to the imported layer. For example, `application services -> application ports` means services import ports, not the other way around.

`core` must not depend on `application` or `infrastructure`.

`infrastructure` must not depend on `application.service.*` implementation modules. It may depend on `application.ports` when implementing application-facing interfaces.

## Layer Responsibilities

### Core

`kairospy/core/` defines stable business concepts:

- Market observations: `Bar`, `Quote`, `TradePrint`, `OrderBookSnapshot`, `RateObservation`, `OptionGreeks`
- Account concepts: `AccountContext`, `AccountSnapshot`, `AccountBalance`, `PositionSnapshot`
- Order concepts: `OrderRequest`, `OrderState`, `OrderEvent`, `VenueOrderResponse`
- Reference concepts: `MarketDefinition`, `InstrumentDefinition`, `ReferenceCatalog`

Core types should be valid without knowing whether the data came from CCXT, Binance SAPI, CSV replay, a database, or a test fixture.

Core should not define persistence row schemas. If a shape exists mainly because JSONL, CSV, Parquet, or `DataStore.write(...)` needs it, it belongs in persistence/storage code, not in core.

### Application Ports

`kairospy/application/ports/` defines what application services need.

Ports should be:

- business-facing
- narrow
- stable
- expressed in core types or application port DTOs

They should not mirror third-party SDK method names unless those names are already domain vocabulary.

Prefer:

```python
class BarHistoryPort(Protocol):
    def bars(self, request: BarHistoryRequest) -> Iterable[Bar]:
        ...
```

Over:

```python
class HistoricalMarketDataClient(Protocol):
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        ...
```

The second shape is acceptable inside an integration gateway, translator, or driver, but it should not be the primary application contract.

Request and response DTOs that exist only to call a use case belong beside the port:

```text
kairospy/application/ports/market_history.py
  BarHistoryRequest
  BarHistoryPort
```

Move a DTO into `core` only when it is a domain concept that remains meaningful without a port. `OrderRequest` is core because it is part of order state and execution history. `BarHistoryRequest` is an application query shape, so it belongs with the port.

Use frozen dataclasses for port DTOs unless the value is intentionally a JSON-like diagnostic payload. Avoid `Mapping[str, object]` for required business parameters.

### Application Services

`kairospy/application/service/` implements use cases and orchestration:

- resolving subscriptions
- bootstrapping account state
- running market streams
- persisting and replaying market data
- coordinating execution services

Application services should depend on application ports and core types. They should not import connector classes directly.

Runtime services are application services. They may depend on `application.ports`, but should not depend on `infrastructure.integrations.protocols` once an application-facing port exists for that capability.

### Infrastructure Integrations

`kairospy/infrastructure/integrations/` implements external system boundaries:

- CCXT driver calls
- exchange REST/websocket calls
- broker account and execution endpoints
- provider historical/reference data endpoints
- raw payload parsing

Integrations may use raw JSON-like values internally:

```python
RawPayload = Mapping[str, object]
IntegrationParams = Mapping[str, object]
```

Raw payload types should stay near the gateway or translator boundary. They should not leak into application services unless the service is explicitly a generic payload browser or diagnostic surface.

Integrations should not write to storage directly. They deliver core domain models or domain events. Application services orchestrate ingestion/persistence, and persistence projectors convert domain models into storage records.

Preferred flow:

```text
CcxtDriver raw payload
  -> BinanceMarketDataConnector
  -> Bar / Quote / TradePrint / MarketEvent
  -> MarketDataIngestService
  -> MarketRecordProjector
  -> BarRecord / QuoteRecord
  -> DataStore
```

Avoid this flow:

```text
BinanceMarketDataConnector
  -> BarRecord
  -> DataStore
```

Integration connectors may expose raw methods for gateway-local, translator-local, or connector tests, but those methods are not application ports. Name them as raw/vendor-facing methods and keep them out of runtime service contracts.

## Integration Protocols

`kairospy/infrastructure/integrations/protocols.py` is transitional.

It can still hold low-level raw connector protocols while the codebase migrates, but new application services should not add dependencies on it. If a service needs a capability, define a narrow port in `kairospy/application/ports/` and make the integration connector or a capability-specific gateway implement that port.

Allowed use of integration protocols:

- typing a driver-facing raw gateway or translator
- connector-level tests
- raw diagnostics and browsing tools

Avoid using integration protocols in:

- strategy runtime
- market runtime services
- account bootstrap services
- execution services
- application facades

Once a capability has an application port, the integration protocol for the same capability should either be removed or renamed to make its raw/vendor role clear.

## Port Granularity

Ports should be smaller, not larger.

Avoid a broad interface like:

```python
class BrokerClient(Protocol):
    def fetch_balance(...): ...
    def fetch_open_orders(...): ...
    def create_order(...): ...
    def cancel_order(...): ...
    def watch_orders(...): ...
```

Prefer capability-specific ports:

```python
class AccountSnapshotGateway(Protocol):
    def snapshot(self, request: AccountSnapshotRequest) -> AccountSnapshot:
        ...


class OpenOrderGateway(Protocol):
    def open_orders(self, request: OpenOrderRequest) -> tuple[OpenOrderSnapshot, ...]:
        ...


class OrderExecutionGateway(Protocol):
    def submit(self, request: OrderSubmissionRequest) -> VenueOrderResponse:
        ...

    def cancel(self, request: OrderCancelRequest) -> VenueOrderResponse:
        ...
```

Composition code can pass one concrete connector object that implements several ports, but services should type against the narrow capability they use.

## Naming Rules

Use domain names in application ports:

- `bars`, not `fetch_ohlcv`
- `quotes`, not `watch_ticker`
- `order_books` or `order_book_events`, not `watch_order_book` if the port returns domain snapshots/events
- `submit`, not `create_order` when the input is an internal order submission request
- `snapshot`, not `fetch_balance` when the result is an `AccountSnapshot`

Use external/API names inside integrations:

- `fetch_ohlcv`
- `fetch_ticker`
- `watch_trades`
- `fetch_balance`
- `create_order`
- `params`

This keeps third-party naming inside the anti-corruption layer.

Use request DTO names that describe intent:

- `BarHistoryRequest`
- `MarketStreamRequest`
- `AccountSnapshotRequest`
- `OpenOrderRequest`
- `OrderSubmissionRequest`
- `OrderCancelRequest`

Do not pass vendor params through application ports as a generic `params` argument. If a vendor override is unavoidable, put it behind an explicitly named escape hatch such as `integration_options` and keep it optional.

## Raw Payload Policy

Raw payloads are allowed in three places:

1. Integration drivers and connector internals
2. Payload translators that convert raw values into core/application types
3. Diagnostic or browsing surfaces whose purpose is to show raw external data

Raw payloads should not be used as normal business return values from application ports.

If raw data must be preserved, attach it explicitly:

```python
MarketEvent(..., metadata={"raw": dict(raw)})
```

or define a field with a clear name:

```python
raw_payload: Mapping[str, object]
```

Do not name raw dictionaries `fields`, `data`, or `result` at a cross-layer boundary unless the boundary is intentionally generic.

If a core domain object keeps external details, store them in explicit metadata:

```python
Quote(..., metadata={"raw_payload": dict(raw)})
```

or in an application/runtime envelope metadata field. Do not add vendor-specific fields to core models just because one connector returns them.

## Persistence Records

Record types such as `BarRecord`, `QuoteRecord`, and `OrderBookRecord` are persistence/interchange schemas, not domain models and not vendor payloads.

They should live with persistence/storage code, for example:

```text
kairospy/infrastructure/persistence/market_data/records.py
```

They are appropriate for:

- `DataStore.write(...)`
- JSONL / CSV / Parquet rows
- dataset import/export
- replay files
- CLI export/import surfaces

They are not appropriate as default application port return values.

Prefer application-facing ports that return core models:

```python
class BarHistoryPort(Protocol):
    def bars(self, request: BarHistoryRequest) -> Iterable[Bar]:
        ...
```

Then project to records only at the persistence boundary:

```python
class MarketRecordProjector(Protocol):
    def bar_record(self, bar: Bar) -> BarRecord:
        ...
```

If a record type is currently in `core`, treat that as transitional. New code should avoid importing storage row schemas from core.

Record projectors/codecs should be pure functions where possible:

```python
def bar_to_record(bar: Bar, *, source: MarketSourceContext) -> BarRecord:
    ...
```

They should not call exchanges, query account state, or mutate stores. Persistence services compose projectors with stores.

## Market Data Persistence

The old broad data package name could mean several things:

- external market data
- data providers
- local storage
- persistence codecs and partitioning

Stores, partitions, ingest writers, row schemas, and codecs now live under the persistence layer:

```text
kairospy/infrastructure/persistence/market_data/
kairospy/infrastructure/storage/
```

Do not begin with a package-wide rename. That creates a large mechanical diff before the dependency direction is fixed. Migrate responsibilities first, then rename if the package role is clear.

## Current Migration Target

The current codebase still has integration protocols that are too close to external APIs. The target is:

```text
kairospy/application/ports/market_history.py
  BarHistoryPort
  FundingRateHistoryPort
  BarHistoryRequest
  FundingRateHistoryRequest

kairospy/application/ports/live_market.py
  QuoteStreamPort
  OrderBookStreamPort
  TradeStreamPort
  MarketStreamRequest

kairospy/infrastructure/integrations/protocols.py
  AccountBalanceClient
  OrderQueryClient
  OrderExecutionClient
  PrivateAccountStream

kairospy/application/runtime/contracts.py
  AccountRuntime
  AccountCatalog
  ExecutionRuntime

kairospy/infrastructure/persistence/market/records.py
  BarRecord
  QuoteRecord
  OrderBookRecord
  TradeRecord
  RateRecord

kairospy/infrastructure/persistence/market/projectors.py
  bar_to_record
  quote_to_record
  order_book_to_record
```

Then:

- application services depend on these ports
- infrastructure connectors or capability-specific gateways implement these ports
- persistence projectors convert core models/events into records and write them
- `infrastructure.integrations.protocols` can shrink to raw driver/connector protocols, or disappear where application ports fully replace it

## Migration Principle: Full Cutover, Not Compatibility

Boundary migrations should not preserve two application-facing contracts for compatibility. The preferred approach is:

1. Analyze the existing architecture first: identify callers, concrete implementations, composition roots, tests, persistence flows, and raw payload boundaries.
2. Define the target port/domain shape before editing call sites.
3. Migrate all affected application services, composition code, integrations, and tests to the target shape in one coherent change.
4. Remove the replaced contract in the same migration.

Do not add long-lived compatibility layers whose purpose is to let old and new application contracts coexist. A temporary bridge is acceptable only when it is part of the same migration and is removed before the migration is considered complete.

Avoid these patterns:

- keeping both `BrokerClient` and narrow account/order ports as supported application contracts
- adding optional fallback paths that call either the old integration protocol or the new application port
- preserving raw SDK-shaped request/response fields in application DTOs to reduce call-site changes
- marking old protocols as deprecated while new application code still depends on them

When a migration is too large for one commit, split it by architecture boundary rather than by compatibility layer. For example, migrate market history ports fully, then account snapshot ports fully, then order execution ports fully. Each slice should leave one target contract for that capability.

## Composition Boundary

Concrete connector selection belongs at composition boundaries:

- CLI command construction
- launch/runtime builder
- integration resolver
- test fixtures

Composition code may import concrete integration classes. After composition, application services should receive only ports or callables typed by ports.

Acceptable:

```python
connector = BinanceMarketDataConnector(...)
service = StreamingMarketDataService(feed=LiveMarketRuntime(connector))
```

Not acceptable in an application service:

```python
from kairospy.infrastructure.integrations.connectors.exchange.binance import BinanceMarketDataConnector
```

This keeps the application service testable with an in-memory port implementation.

## Migration Steps

1. Map the current callers, implementations, composition roots, and tests for one capability slice.
2. Define the target core types, request DTOs, narrow ports, and persistence projectors for that slice.
3. Move persistence record types and projectors out of `core` into `infrastructure/persistence/market_data`.
4. Add or update request DTOs and narrow ports under `application/ports`.
5. Change all affected application services to consume the new ports and core models/events.
6. Update infrastructure connectors or capability-specific gateways to implement those ports.
7. Move connector `persist_*` methods into application services or persistence-oriented services.
8. Add persistence projectors/codecs that convert core models/events into records.
9. Update composition roots and tests to use the target contracts.
10. Remove replaced broad integration protocols such as `BrokerClient` once the slice has moved.
11. Remove service imports from infrastructure.
12. Keep raw aliases only in raw integration boundaries such as `infrastructure.integrations.types`.
13. Remove broad storage aliases after each slice has moved to the target persistence package.

## Decision Checklist

When adding a new integration method, ask:

- Is this a core business concept? Put the type in `core`.
- Is this a storage/import/export row schema? Put it in persistence/storage, not core.
- Is this what an application service needs? Put the protocol/request DTO in `application.ports`.
- Is this a third-party SDK shape or raw HTTP payload? Keep it in `infrastructure.integrations`.
- Does the method return `Mapping[str, object]`? If yes, is it raw, diagnostic, or intentionally generic? If not, define a domain type.
- Does an application service import `infrastructure.integrations.*`? Prefer depending on a port instead.
- Does an integration connector write directly to a store? Move that orchestration into an application service.
- Does infrastructure import `application.service.*`? Move the shared type/function to `core` or an application port.
- Is this file only selecting concrete implementations? It can live in composition/factory code and import infrastructure.
