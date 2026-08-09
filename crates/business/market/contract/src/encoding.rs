//! Market snapshot encoding and shared-memory publication.

use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, SnapshotHeader, SnapshotHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_market_data_snapshot_buffer, finish_order_book_snapshot_buffer, Bar as FbBar,
    BarArgs as FbBarArgs, Greeks as FbGreeks, GreeksArgs as FbGreeksArgs, MarketData,
    MarketDataArgs, MarketDataSnapshot, MarketDataSnapshotArgs, OrderBook as FbOrderBook,
    OrderBookArgs as FbOrderBookArgs, OrderBookLevel as FbOrderBookLevel,
    OrderBookLevelArgs as FbOrderBookLevelArgs, OrderBookSnapshot, OrderBookSnapshotArgs,
    OrderBooks as FbOrderBooks, OrderBooksArgs as FbOrderBooksArgs, Quote as FbQuote,
    QuoteArgs as FbQuoteArgs, Trade as FbTrade, TradeArgs as FbTradeArgs,
};
use kairos_protocol::InstanceIdentity;
use kairos_transport::{SharedSnapshotWriter, SharedSnapshotWriter as ViewSnapshotWriter};

use crate::model::{MarketObservation, MarketSnapshot, MarketViewKey, OrderBook, PriceLevel};

pub struct MmapMarketSnapshotPublisher {
    writer: SharedSnapshotWriter,
    root_path: std::path::PathBuf,
    slot_size: usize,
    view_writers: std::collections::BTreeMap<String, ViewSnapshotWriter>,
    orderbook_publishers: std::collections::BTreeMap<String, MmapOrderBookSnapshotPublisher>,
    actor_id: String,
    event_stream_id: String,
    identity: InstanceIdentity,
}

impl MmapMarketSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> std::io::Result<Self> {
        Self::create_with_identity(
            path,
            slot_size,
            actor_id,
            event_stream_id,
            InstanceIdentity::default(),
        )
    }

    pub fn create_with_identity(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> std::io::Result<Self> {
        let path = path.as_ref().to_path_buf();
        let root_path = path
            .parent()
            .map(std::path::Path::to_path_buf)
            .unwrap_or_else(|| std::path::PathBuf::from("."));
        Ok(Self {
            writer: SharedSnapshotWriter::create(&path, slot_size)?,
            root_path,
            slot_size,
            view_writers: std::collections::BTreeMap::new(),
            orderbook_publishers: std::collections::BTreeMap::new(),
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
            identity,
        })
    }

    pub fn encode(&self, snapshot: &MarketSnapshot) -> Result<Vec<u8>, String> {
        self.encode_view(
            snapshot,
            "market.current",
            snapshot.latest.values().collect(),
        )
    }

    fn encode_view(
        &self,
        snapshot: &MarketSnapshot,
        view_key: &str,
        observations: Vec<&MarketObservation>,
    ) -> Result<Vec<u8>, String> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let mut quotes = Vec::new();
        let mut trades = Vec::new();
        let mut bars = Vec::new();
        let mut greeks = Vec::new();
        for observation in observations.iter().copied() {
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
                MarketObservation::Bar(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let timeframe = builder.create_string(&value.timeframe);
                    let source_id = builder.create_string(&value.source_id);
                    let derivation = builder.create_string(&value.derivation);
                    let open = decimal64(&mut builder, Some(&value.open))
                        .ok_or_else(|| "bar open is not a decimal".to_string())?;
                    let high = decimal64(&mut builder, Some(&value.high))
                        .ok_or_else(|| "bar high is not a decimal".to_string())?;
                    let low = decimal64(&mut builder, Some(&value.low))
                        .ok_or_else(|| "bar low is not a decimal".to_string())?;
                    let close = decimal64(&mut builder, Some(&value.close))
                        .ok_or_else(|| "bar close is not a decimal".to_string())?;
                    let volume = decimal64(&mut builder, value.volume.as_deref());
                    bars.push(FbBar::create(
                        &mut builder,
                        &FbBarArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            timeframe: Some(timeframe),
                            open: Some(&open),
                            high: Some(&high),
                            low: Some(&low),
                            close: Some(&close),
                            volume: volume.as_ref(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                            derivation: Some(derivation),
                        },
                    ));
                }
                MarketObservation::OptionGreeks(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let source_id = builder.create_string(&value.source_id);
                    let derivation = builder.create_string(&value.derivation);
                    let strike = decimal64(&mut builder, value.strike.as_deref());
                    let delta = decimal64(&mut builder, value.delta.as_deref());
                    let gamma = decimal64(&mut builder, value.gamma.as_deref());
                    let vega = decimal64(&mut builder, value.vega.as_deref());
                    let theta = decimal64(&mut builder, value.theta.as_deref());
                    let implied_volatility =
                        decimal64(&mut builder, value.implied_volatility.as_deref());
                    greeks.push(FbGreeks::create(
                        &mut builder,
                        &FbGreeksArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            expiry_unix_nanos: value.expiry_unix_nanos.unwrap_or_default(),
                            strike: strike.as_ref(),
                            delta: delta.as_ref(),
                            gamma: gamma.as_ref(),
                            vega: vega.as_ref(),
                            theta: theta.as_ref(),
                            implied_volatility: implied_volatility.as_ref(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                            derivation: Some(derivation),
                        },
                    ));
                }
            }
        }
        let quotes = builder.create_vector(&quotes);
        let trades = builder.create_vector(&trades);
        let bars = builder.create_vector(&bars);
        let greeks = builder.create_vector(&greeks);
        let payload = MarketData::create(
            &mut builder,
            &MarketDataArgs {
                quote_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::Quote(_)))
                    .count() as u64,
                trade_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::Trade(_)))
                    .count() as u64,
                bar_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::Bar(_)))
                    .count() as u64,
                greeks_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::OptionGreeks(_)))
                    .count() as u64,
                quotes: Some(quotes),
                trades: Some(trades),
                bars: Some(bars),
                greeks: Some(greeks),
                ..Default::default()
            },
        );
        let snapshot_id = builder.create_string(&format!("{}:{}", view_key, snapshot.generation));
        let view_key = builder.create_string(view_key);
        let actor_id = builder.create_string(&self.actor_id);
        let event_stream_id = builder.create_string(&self.event_stream_id);
        let workspace_id = non_empty_string(&mut builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(&mut builder, &self.identity.launch_id);
        let instance_id = non_empty_string(&mut builder, &self.identity.instance_id);
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(actor_id),
                event_stream_id: Some(event_stream_id),
                workspace_id,
                launch_id,
                instance_id,
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
            .map_err(|error| error.to_string())?;

        for (view_key, observation) in &snapshot.views {
            let view = observation.view_key().map_err(|error| error.to_string())?;
            let path = self.view_path(&view)?;
            let payload = self.encode_view(snapshot, view_key, vec![observation])?;
            let writer = match self.view_writers.entry(view_key.clone()) {
                std::collections::btree_map::Entry::Occupied(entry) => entry.into_mut(),
                std::collections::btree_map::Entry::Vacant(entry) => {
                    if let Some(parent) = path.parent() {
                        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
                    }
                    entry.insert(
                        ViewSnapshotWriter::create(&path, self.slot_size)
                            .map_err(|error| error.to_string())?,
                    )
                }
            };
            writer
                .publish(snapshot.generation, &payload)
                .map_err(|error| error.to_string())?;
        }

        for book in snapshot.order_books.values() {
            let source_id = snapshot
                .subscriptions
                .iter()
                .flat_map(|subscription| subscription.members.values())
                .find(|market| market.market_id == book.market_id)
                .map(|market| market.venue_id.as_str())
                .unwrap_or("market");
            let view = MarketViewKey::new(source_id, &book.market_id, "orderbook")
                .map_err(|error| error.to_string())?;
            let path = self.view_path(&view)?;
            let publisher = match self.orderbook_publishers.entry(view.as_str().to_string()) {
                std::collections::btree_map::Entry::Occupied(entry) => entry.into_mut(),
                std::collections::btree_map::Entry::Vacant(entry) => {
                    if let Some(parent) = path.parent() {
                        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
                    }
                    entry.insert(
                        MmapOrderBookSnapshotPublisher::create_with_identity(
                            &path,
                            self.slot_size,
                            &snapshot.actor_id,
                            "market.events",
                            self.identity.clone(),
                        )
                        .map_err(|error| error.to_string())?,
                    )
                }
            };
            let mut books = std::collections::BTreeMap::new();
            books.insert(book.market_id.clone(), book.clone());
            publisher.publish_books_with_view_key(
                snapshot.generation,
                snapshot.event_sequence,
                &books,
                &view.as_str(),
                source_id,
            )?;
        }
        Ok(())
    }

    fn view_path(&self, view: &MarketViewKey) -> Result<std::path::PathBuf, String> {
        let mut path = self.root_path.join("views");
        for part in view.path_parts() {
            path.push(safe_path_part(part)?);
        }
        Ok(path.join("current.snapshot"))
    }
}

fn safe_path_part(value: &str) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty()
        || value == "."
        || value == ".."
        || value.contains('/')
        || value.contains('\\')
    {
        return Err("market view identity contains an invalid path component".into());
    }
    Ok(value.to_owned())
}

pub struct MmapOrderBookSnapshotPublisher {
    writer: SharedSnapshotWriter,
    actor_id: String,
    event_stream_id: String,
    identity: InstanceIdentity,
}

impl MmapOrderBookSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> std::io::Result<Self> {
        Self::create_with_identity(
            path,
            slot_size,
            actor_id,
            event_stream_id,
            InstanceIdentity::default(),
        )
    }

    pub fn create_with_identity(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> std::io::Result<Self> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size)?,
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
            identity,
        })
    }

    pub fn publish_books(
        &mut self,
        generation: u64,
        event_sequence: u64,
        books: &std::collections::BTreeMap<String, OrderBook>,
    ) -> Result<(), String> {
        self.publish_books_with_view_key(
            generation,
            event_sequence,
            books,
            "market.orderbook",
            "market",
        )
    }

    pub fn publish_books_with_view_key(
        &mut self,
        generation: u64,
        event_sequence: u64,
        books: &std::collections::BTreeMap<String, OrderBook>,
        view_key_value: &str,
        source_id_value: &str,
    ) -> Result<(), String> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let mut encoded = Vec::new();
        for book in books.values() {
            let market_id = builder.create_string(&book.market_id);
            let instrument_id = builder.create_string(&book.instrument_id);
            let source_id = builder.create_string(source_id_value);
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
        let snapshot_id = builder.create_string(&format!("{}:{generation}", view_key_value));
        let view_key = builder.create_string(view_key_value);
        let actor_id = builder.create_string(&self.actor_id);
        let stream = builder.create_string(&self.event_stream_id);
        let workspace_id = non_empty_string(&mut builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(&mut builder, &self.identity.launch_id);
        let instance_id = non_empty_string(&mut builder, &self.identity.instance_id);
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(actor_id),
                event_stream_id: Some(stream),
                workspace_id,
                launch_id,
                instance_id,
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

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut flatbuffers::FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

fn encode_levels<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut flatbuffers::FlatBufferBuilder<'a, A>,
    levels: &[PriceLevel],
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
