# Wire protocol conventions

The schemas in this directory are process-boundary contracts. They are not
the Python or Rust domain model and generated types must not leak through
application APIs.

Published snapshot schemas are owned by Actors. The owning Actor is the
single writer and publishes immutable generations; strategies and query
processes are consumers. Actor ownership is part of the protocol contract,
not an implementation detail of a particular runtime.

## Business vocabulary

Namespaces are business-oriented: `reference`, `market`, `account`,
`execution`, `risk`, `intent`, and `system`. The publication files remain
grouped under `projection/` for now, but `projection` is a delivery shape,
not part of the wire vocabulary.

Business types live in the owning domain namespace and are reusable. Published
snapshot roots only aggregate those types and add the common snapshot header.
For example, a market snapshot contains `market.Quote`, `market.Trade`, and
`market.Bar`; it does not redefine `QuoteState` or `TradeState`.

## Message boundaries

Every independently published message has its own FlatBuffers root and
four-byte file identifier. Business messages are not combined into a large
union. Commands and events are separate messages, even when they concern the
same entity.

Each message contains a common `MessageHeader` and one payload table. The
header owns message identity, producer identity, stream identity, sequence,
event time, and publish time. The payload owns only business data.

## Evolution

Additive fields are preferred. A breaking change creates a new `vN` schema
directory and a new file identifier. A message's file identifier and
namespace identify its wire contract; there is no global schema version shared
by unrelated domains.

## Transport

The transport owns framing, routing, delivery, and backpressure. It must not
introduce a business union. The current Unix adapter uses a four-byte network
order length prefix. Aeron can replace that adapter while keeping the
FlatBuffers roots unchanged.
