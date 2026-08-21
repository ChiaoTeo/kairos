# Market wire contract v2

Market v2 has three distinct contract families:

```text
events/  immutable business facts and lifecycle facts published on the stream
views/   bounded latest-state images published through KSS1 mmap resources
types/   semantic value groups shared by multiple roots within the same wire family
```

The `types/` directory is intentionally grouped by semantic family rather
than by individual table. A table that represents a current view wrapper lives
beside its view root; it is not shared with an event merely because its fields
look similar. Types used by only one root stay in that root file. Freshness
vocabulary is local to `MarketFreshnessLatestView`; subscription lifecycle is
returned by the control command and has no stream event.

The UDS control surface is not a FlatBuffers root. It is specified by the
Market Rust JSON-RPC contract trait and uses typed command, status, error, and
idempotency semantics over the workspace Unix socket.
Runtime and owner isolation are defined by [`isolation.md`](./isolation.md);
`ObservationScope` and `source_id` are business/provenance identities, not
process isolation boundaries. Venue observations use a canonical `market_id`;
consolidated observations use `instrument_id` plus an optional network.

Runtime adoption is evidenced by the owning module's contract, publication and
architecture tests. Generated bindings alone do not mean that the running
Market process publishes v2.

## Market v2 surface

Events:

- `QuoteUpdated`
- `TradeOccurred`
- `BarCompleted`
- `GreeksUpdated`
- `RateUpdated`
- `Ticker24hUpdated`
- `MarkPriceUpdated`
- `FundingRateUpdated`
- `OpenInterestUpdated`
- `IndexPriceUpdated`
- `OrderBookSnapshotReceived`
- `OrderBookDeltaReceived`
- `OrderBookResyncRequired`

Current views:

- `QuoteLatestView`
- `BarWindowView`
- `GreeksLatestView`
- `RateLatestView`
- `Ticker24hLatestView`
- `MarkPriceLatestView`
- `FundingRateLatestView`
- `OpenInterestLatestView`
- `IndexPriceLatestView`
- `OrderBookLatestView`
- `MarketFreshnessLatestView`

An event is an immutable past fact. A latest view is one latest value per
identity; a window view is a bounded ordered set of values. View generations
and source event identifiers are evidence about the image; they are not
event-stream cursors or recovery positions.

Event roots use past-tense verb phrases. `Updated`, `Occurred`, `Completed`,
and `Received` describe facts accepted by Market, while `Required` describes a
fact that recovery is needed. The underlying payload types remain business
nouns such as `Quote`, `Bar`, and `OrderBookLevel`.

OrderBook is one semantic family, not one overloaded message: shared identity
and level types live in `types/order_book.fbs`; snapshot, delta, and resync are
separate stream facts; `OrderBookLatestView` contains one `book` for one
order-book identity. This
keeps replay and recovery explicit without duplicating the order-book model.

Subscription commands complete synchronously: a successful response means the
subscription is established and its requested view is readable. Failure or
deadline expiry is returned as a command error; there is no subscription
status event.
