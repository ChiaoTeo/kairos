# Integration connections

Integration manages external permission-scoped links. A public application
connection represents exactly one HTTP or WebSocket link:

```text
Integration.connect(ConnectionSpec)
    -> one Connection
         ├── one participant
         ├── one access scope
         ├── one transport
         └── business methods implemented by that connection
```

The public model intentionally does not add `Access`, `Capability`, or
`Session` wrapper objects. A connection owns its lifecycle:

```python
connection = integration.connect(spec)
await connection.start()
connection.health()
await connection.reconnect()
await connection.stop()
```

`IntegrationConnectionSpec` contains one participant, one product, one
`AccessScope`, and one `TransportKind`. Public and private connections are
separate links. An implementation may share an underlying pool internally,
but it must not expose one connection across permission contexts.

## Business protocols

Business protocols are small and composable. They are implemented directly by
the connection that can provide them:

- `MarketDataConnection`: bars and historical market data;
- `ReferenceDataConnection`: instrument and trading-rule catalog;
- `MarketStreamConnection`: public market subscriptions;
- `AccountConnection`: account bootstrap/snapshot;
- `AccountStreamConnection`: account updates;
- `OrderConnection`: submit and cancel orders;
- `OrderUpdateConnection`: order and fill updates.

One connection can implement several related protocols. For example, a
private REST connection can provide account reads and order entry because both
use the same authenticated request link. This is implementation reuse, not a
second public capability layer.

## Binance Spot mapping

```text
BinanceSpotPublicRestConnection
  -> bars / catalog

BinanceSpotPublicStreamConnection
  -> market subscriptions

BinanceSpotPrivateRestConnection
  -> account bootstrap / submit / cancel

BinanceSpotPrivateStreamConnection
  -> account snapshots / order updates / trade fills
```

Binance security types map to the Integration access model as follows:

```text
NONE        -> PUBLIC + REST or market stream
USER_DATA   -> PRIVATE + REST
TRADE       -> PRIVATE + REST or request API
USER_STREAM -> PRIVATE + user stream
```

The business method does not expose Binance parameters or payloads. Vendor
translation remains inside the concrete connection and its private services.
