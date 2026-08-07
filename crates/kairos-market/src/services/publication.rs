//! Market snapshot encoding and shared-memory publication.

use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, SnapshotHeader, SnapshotHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_market_data_snapshot_buffer, finish_order_book_snapshot_buffer, MarketData,
    MarketDataArgs, MarketDataSnapshot, MarketDataSnapshotArgs, OrderBook as FbOrderBook,
    OrderBookArgs as FbOrderBookArgs, OrderBookLevel as FbOrderBookLevel,
    OrderBookLevelArgs as FbOrderBookLevelArgs, OrderBookSnapshot, OrderBookSnapshotArgs,
    OrderBooks as FbOrderBooks, OrderBooksArgs as FbOrderBooksArgs, Quote as FbQuote,
    QuoteArgs as FbQuoteArgs, Trade as FbTrade, TradeArgs as FbTradeArgs,
};
use kairos_transport::SharedSnapshotWriter;

use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::OrderBook;
use crate::services::actor::MarketSnapshot;

pub struct MmapMarketSnapshotPublisher {
    writer: SharedSnapshotWriter,
    actor_id: String,
    event_stream_id: String,
}

impl MmapMarketSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> std::io::Result<Self> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size)?,
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
        })
    }

    pub fn encode(&self, snapshot: &MarketSnapshot) -> Result<Vec<u8>, String> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let mut quotes = Vec::new();
        let mut trades = Vec::new();
        for observation in snapshot.latest.values() {
            match observation {
                MarketObservation::Quote(value) => {
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let market_id = builder.create_string(&value.market_id);
                    let source_id = builder.create_string(&value.source_id);
                    let bid_price = decimal64(&mut builder, value.bid_price.as_deref());
                    let bid_quantity = decimal64(&mut builder, value.bid_quantity.as_deref());
                    let ask_price = decimal64(&mut builder, value.ask_price.as_deref());
                    let ask_quantity = decimal64(&mut builder, value.ask_quantity.as_deref());
                    quotes.push(FbQuote::create(
                        &mut builder,
                        &FbQuoteArgs {
                            instrument_id: Some(instrument_id),
                            market_id: Some(market_id),
                            bid_price: bid_price.as_ref(),
                            bid_quantity: bid_quantity.as_ref(),
                            ask_price: ask_price.as_ref(),
                            ask_quantity: ask_quantity.as_ref(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                            ..Default::default()
                        },
                    ));
                }
                MarketObservation::Trade(value) => {
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let market_id = builder.create_string(&value.market_id);
                    let price = decimal64(&mut builder, Some(&value.price))
                        .ok_or_else(|| "trade price is not a decimal".to_string())?;
                    let quantity = decimal64(&mut builder, Some(&value.quantity))
                        .ok_or_else(|| "trade quantity is not a decimal".to_string())?;
                    let source_id = builder.create_string(&value.source_id);
                    let trade_id = value.trade_id.as_ref().map(|id| builder.create_string(id));
                    trades.push(FbTrade::create(
                        &mut builder,
                        &FbTradeArgs {
                            trade_id,
                            instrument_id: Some(instrument_id),
                            market_id: Some(market_id),
                            price: Some(&price),
                            quantity: Some(&quantity),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                            ..Default::default()
                        },
                    ));
                }
            }
        }
        let quotes = builder.create_vector(&quotes);
        let trades = builder.create_vector(&trades);
        let payload = MarketData::create(
            &mut builder,
            &MarketDataArgs {
                quote_count: snapshot
                    .latest
                    .values()
                    .filter(|v| matches!(v, MarketObservation::Quote(_)))
                    .count() as u64,
                trade_count: snapshot
                    .latest
                    .values()
                    .filter(|v| matches!(v, MarketObservation::Trade(_)))
                    .count() as u64,
                quotes: Some(quotes),
                trades: Some(trades),
                ..Default::default()
            },
        );
        let snapshot_id = builder.create_string(&format!("market:{}", snapshot.generation));
        let view_key = builder.create_string("market.current");
        let actor_id = builder.create_string(&self.actor_id);
        let event_stream_id = builder.create_string(&self.event_stream_id);
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(actor_id),
                event_stream_id: Some(event_stream_id),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
                event_sequence: snapshot.event_sequence,
                version: snapshot.generation,
                generation: snapshot.generation,
                generated_at_unix_nanos: now_unix_nanos(),
                as_of_unix_nanos: now_unix_nanos(),
                complete: true,
            },
        );
        let root = MarketDataSnapshot::create(
            &mut builder,
            &MarketDataSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        finish_market_data_snapshot_buffer(&mut builder, root);
        Ok(builder.finished_data().to_vec())
    }
}

impl MmapMarketSnapshotPublisher {
    pub fn publish(&mut self, snapshot: &MarketSnapshot) -> Result<(), String> {
        let payload = self.encode(snapshot)?;
        self.writer
            .publish(snapshot.generation, &payload)
            .map_err(|error| error.to_string())
    }
}

pub struct MmapOrderBookSnapshotPublisher {
    writer: SharedSnapshotWriter,
    actor_id: String,
    event_stream_id: String,
}

impl MmapOrderBookSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> std::io::Result<Self> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size)?,
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
        })
    }

    pub fn publish_books(
        &mut self,
        generation: u64,
        event_sequence: u64,
        books: &std::collections::BTreeMap<String, OrderBook>,
    ) -> Result<(), String> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let mut encoded = Vec::new();
        for book in books.values() {
            let market_id = builder.create_string(&book.market_id);
            let instrument_id = builder.create_string(&book.instrument_id);
            let source_id = builder.create_string("market");
            let bids = encode_levels(&mut builder, &book.bids)?;
            let asks = encode_levels(&mut builder, &book.asks)?;
            encoded.push(FbOrderBook::create(
                &mut builder,
                &FbOrderBookArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    sequence: book.sequence,
                    event_time_unix_nanos: book.event_time_unix_nanos,
                    source_id: Some(source_id),
                    synchronized: book.synchronized,
                    bids: Some(bids),
                    asks: Some(asks),
                    ..Default::default()
                },
            ));
        }
        let books_vector = builder.create_vector(&encoded);
        let payload = FbOrderBooks::create(
            &mut builder,
            &FbOrderBooksArgs {
                book_count: books.len() as u64,
                books: Some(books_vector),
            },
        );
        let snapshot_id = builder.create_string(&format!("market-books:{generation}"));
        let view_key = builder.create_string("market.orderbook");
        let actor_id = builder.create_string(&self.actor_id);
        let stream = builder.create_string(&self.event_stream_id);
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(actor_id),
                event_stream_id: Some(stream),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
                event_sequence,
                version: generation,
                generation,
                generated_at_unix_nanos: now_unix_nanos(),
                as_of_unix_nanos: now_unix_nanos(),
                complete: true,
            },
        );
        let root = OrderBookSnapshot::create(
            &mut builder,
            &OrderBookSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        finish_order_book_snapshot_buffer(&mut builder, root);
        self.writer
            .publish(generation, builder.finished_data())
            .map_err(|error| error.to_string())
    }
}

fn encode_levels<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut flatbuffers::FlatBufferBuilder<'a, A>,
    levels: &[crate::domain::orderbook::PriceLevel],
) -> Result<
    flatbuffers::WIPOffset<
        flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<FbOrderBookLevel<'a>>>,
    >,
    String,
> {
    let values: Vec<_> = levels
        .iter()
        .map(|level| {
            let price = decimal64(builder, Some(&level.price))
                .ok_or_else(|| "order book price is not a decimal".to_string())?;
            let quantity = decimal64(builder, Some(&level.quantity))
                .ok_or_else(|| "order book quantity is not a decimal".to_string())?;
            Ok(FbOrderBookLevel::create(
                builder,
                &FbOrderBookLevelArgs {
                    price: Some(&price),
                    quantity: Some(&quantity),
                    ..Default::default()
                },
            ))
        })
        .collect::<Result<_, String>>()?;
    Ok(builder.create_vector(&values))
}

fn decimal64<'a, A: flatbuffers::Allocator + 'a>(
    _builder: &mut flatbuffers::FlatBufferBuilder<'a, A>,
    value: Option<&str>,
) -> Option<Decimal64> {
    let value = value?;
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    let digits = format!("{whole}{fraction}");
    let mantissa = digits.parse::<i64>().ok()?;
    Some(Decimal64::new(mantissa, fraction.len() as u8))
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
