//! Market snapshot encoding and shared-memory publication.

use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, SnapshotHeader, SnapshotHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_market_data_snapshot_buffer, finish_order_book_snapshot_buffer, Bar as FbBar,
    BarArgs as FbBarArgs, FundingRate as FbFundingRate, FundingRateArgs as FbFundingRateArgs,
    Greeks as FbGreeks, GreeksArgs as FbGreeksArgs, IndexPrice as FbIndexPrice,
    IndexPriceArgs as FbIndexPriceArgs, InstrumentStatus as FbInstrumentStatus,
    InstrumentStatusArgs as FbInstrumentStatusArgs, MarkPrice as FbMarkPrice,
    MarkPriceArgs as FbMarkPriceArgs, MarketData, MarketDataArgs, MarketDataSnapshot,
    MarketDataSnapshotArgs, MarketFreshness as FbMarketFreshness,
    MarketFreshnessArgs as FbMarketFreshnessArgs, OpenInterest as FbOpenInterest,
    OpenInterestArgs as FbOpenInterestArgs, OrderBook as FbOrderBook,
    OrderBookArgs as FbOrderBookArgs, OrderBookLevel as FbOrderBookLevel,
    OrderBookLevelArgs as FbOrderBookLevelArgs, OrderBookSnapshot, OrderBookSnapshotArgs,
    OrderBooks as FbOrderBooks, OrderBooksArgs as FbOrderBooksArgs, Quote as FbQuote,
    QuoteArgs as FbQuoteArgs, Rate as FbRate, RateArgs as FbRateArgs, Ticker24h as FbTicker24h,
    Ticker24hArgs as FbTicker24hArgs, Trade as FbTrade, TradeArgs as FbTradeArgs,
};
use kairos_protocol::InstanceIdentity;
use kairos_transport::{SharedSnapshotWriter, SharedSnapshotWriter as ViewSnapshotWriter};

use crate::model::{MarketCurrentView, MarketObservation, MarketViewKey, OrderBook, PriceLevel};

pub struct MmapMarketSnapshotPublisher {
    writer: SharedSnapshotWriter,
    root_path: std::path::PathBuf,
    slot_size: usize,
    view_writers: std::collections::BTreeMap<String, ViewSnapshotWriter>,
    orderbook_publishers: std::collections::BTreeMap<String, MmapOrderBookSnapshotPublisher>,
    actor_id: String,
    identity: InstanceIdentity,
}

impl MmapMarketSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> std::io::Result<Self> {
        Self::create_with_identity(path, slot_size, actor_id, InstanceIdentity::default())
    }

    pub fn create_with_identity(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
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
            identity,
        })
    }

    pub fn encode(&self, snapshot: &MarketCurrentView) -> Result<Vec<u8>, String> {
        self.encode_view(
            snapshot,
            "market.current",
            snapshot.views.values().collect(),
        )
    }

    fn encode_view(
        &self,
        snapshot: &MarketCurrentView,
        view_key: &str,
        observations: Vec<&MarketObservation>,
    ) -> Result<Vec<u8>, String> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let mut quotes = Vec::new();
        let mut trades = Vec::new();
        let mut bars = Vec::new();
        let mut greeks = Vec::new();
        let mut rates = Vec::new();
        let mut ticker_24h = Vec::new();
        let mut mark_prices = Vec::new();
        let mut index_prices = Vec::new();
        let mut funding_rates = Vec::new();
        let mut open_interests = Vec::new();
        let mut freshness = Vec::new();
        let mut instrument_statuses = Vec::new();
        for value in snapshot.freshness.values() {
            let source_id = builder.create_string(&value.source_id);
            let market_id = builder.create_string(&value.market_id);
            let data_kind = builder.create_string(&value.data_kind);
            let status = builder.create_string(match value.status {
                crate::model::DataFreshnessStatus::Unknown => "unknown",
                crate::model::DataFreshnessStatus::Current => "current",
                crate::model::DataFreshnessStatus::Stale => "stale",
            });
            freshness.push(FbMarketFreshness::create(
                &mut builder,
                &FbMarketFreshnessArgs {
                    source_id: Some(source_id),
                    market_id: Some(market_id),
                    data_kind: Some(data_kind),
                    last_event_time_unix_nanos: value.last_event_time_unix_nanos,
                    last_received_time_unix_nanos: value.last_received_time_unix_nanos,
                    status: Some(status),
                },
            ));
        }
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
                    let cost = decimal64(&mut builder, value.cost.as_deref());
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
                            cost: cost.as_ref(),
                            aggressor_side: aggressor_side(value.aggressor_side.as_deref()),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
                MarketObservation::Bar(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let timeframe = builder.create_string(&value.timeframe);
                    let source_id = builder.create_string(&value.source_id);
                    let derivation = builder.create_string(&value.derivation);
                    let bar_kind = builder.create_string("bar");
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
                            bar_kind: Some(bar_kind),
                        },
                    ));
                }
                MarketObservation::TradeBar(value) => {
                    bars.push(encode_bar_record(&mut builder, &value.bar, "trade_bar")?);
                }
                MarketObservation::QuoteBar(value) => {
                    bars.push(encode_bar_record(&mut builder, &value.bar, "quote_bar")?);
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
                MarketObservation::Rate(value) => {
                    let rate_id = builder.create_string(&value.rate_id);
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let basis = builder.create_string(&value.basis);
                    let source_id = builder.create_string(&value.source_id);
                    let value_decimal = decimal64(&mut builder, Some(&value.value))
                        .ok_or_else(|| "rate value is not a decimal".to_string())?;
                    let mark_price = decimal64(&mut builder, value.mark_price.as_deref());
                    rates.push(FbRate::create(
                        &mut builder,
                        &FbRateArgs {
                            rate_id: Some(rate_id),
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            basis: Some(basis),
                            value: Some(&value_decimal),
                            mark_price: mark_price.as_ref(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
                MarketObservation::Ticker24h(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let source_id = builder.create_string(&value.source_id);
                    let last_price = decimal64(&mut builder, value.last_price.as_deref());
                    let bid_price = decimal64(&mut builder, value.bid_price.as_deref());
                    let bid_quantity = decimal64(&mut builder, value.bid_quantity.as_deref());
                    let ask_price = decimal64(&mut builder, value.ask_price.as_deref());
                    let ask_quantity = decimal64(&mut builder, value.ask_quantity.as_deref());
                    let open_price = decimal64(&mut builder, value.open_price.as_deref());
                    let high_price = decimal64(&mut builder, value.high_price.as_deref());
                    let low_price = decimal64(&mut builder, value.low_price.as_deref());
                    let volume_base = decimal64(&mut builder, value.volume_base.as_deref());
                    let volume_quote = decimal64(&mut builder, value.volume_quote.as_deref());
                    let price_change_abs =
                        decimal64(&mut builder, value.price_change_abs.as_deref());
                    let price_change_pct =
                        decimal64(&mut builder, value.price_change_pct.as_deref());
                    let vwap = decimal64(&mut builder, value.vwap.as_deref());
                    let mark_price = decimal64(&mut builder, value.mark_price.as_deref());
                    ticker_24h.push(FbTicker24h::create(
                        &mut builder,
                        &FbTicker24hArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            last_price: last_price.as_ref(),
                            bid_price: bid_price.as_ref(),
                            bid_quantity: bid_quantity.as_ref(),
                            ask_price: ask_price.as_ref(),
                            ask_quantity: ask_quantity.as_ref(),
                            open_price: open_price.as_ref(),
                            high_price: high_price.as_ref(),
                            low_price: low_price.as_ref(),
                            volume_base: volume_base.as_ref(),
                            volume_quote: volume_quote.as_ref(),
                            price_change_abs: price_change_abs.as_ref(),
                            price_change_pct: price_change_pct.as_ref(),
                            vwap: vwap.as_ref(),
                            mark_price: mark_price.as_ref(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
                MarketObservation::MarkPrice(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let source_id = builder.create_string(&value.source_id);
                    let mark_price = decimal64(&mut builder, Some(&value.mark_price))
                        .ok_or_else(|| "mark price is not a decimal".to_string())?;
                    let index_price = decimal64(&mut builder, value.index_price.as_deref());
                    let settlement =
                        decimal64(&mut builder, value.estimated_settlement_price.as_deref());
                    let funding = decimal64(&mut builder, value.funding_rate.as_deref());
                    mark_prices.push(FbMarkPrice::create(
                        &mut builder,
                        &FbMarkPriceArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            mark_price: Some(&mark_price),
                            index_price: index_price.as_ref(),
                            estimated_settlement_price: settlement.as_ref(),
                            funding_rate: funding.as_ref(),
                            next_funding_time_unix_nanos: value
                                .next_funding_time_unix_nanos
                                .unwrap_or_default(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
                MarketObservation::IndexPrice(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let source_id = builder.create_string(&value.source_id);
                    let spot = decimal64(&mut builder, value.spot_index_price.as_deref());
                    let contract = decimal64(&mut builder, value.contract_index_price.as_deref());
                    let index = decimal64(&mut builder, value.index_price.as_deref());
                    let funding = decimal64(&mut builder, value.funding_rate.as_deref());
                    index_prices.push(FbIndexPrice::create(
                        &mut builder,
                        &FbIndexPriceArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            spot_index_price: spot.as_ref(),
                            contract_index_price: contract.as_ref(),
                            index_price: index.as_ref(),
                            funding_rate: funding.as_ref(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
                MarketObservation::FundingRate(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let source_id = builder.create_string(&value.source_id);
                    let rate = decimal64(&mut builder, Some(&value.funding_rate))
                        .ok_or_else(|| "funding rate is not a decimal".to_string())?;
                    funding_rates.push(FbFundingRate::create(
                        &mut builder,
                        &FbFundingRateArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            funding_rate: Some(&rate),
                            funding_period_seconds: value
                                .funding_period_seconds
                                .unwrap_or_default(),
                            next_funding_time_unix_nanos: value
                                .next_funding_time_unix_nanos
                                .unwrap_or_default(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
                MarketObservation::OpenInterest(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let source_id = builder.create_string(&value.source_id);
                    let contracts = decimal64(&mut builder, Some(&value.contracts))
                        .ok_or_else(|| "open interest contracts is not a decimal".to_string())?;
                    let quote = decimal64(&mut builder, value.quote_value.as_deref());
                    let change = decimal64(&mut builder, value.change_24h.as_deref());
                    let change_pct = decimal64(&mut builder, value.change_pct_24h.as_deref());
                    open_interests.push(FbOpenInterest::create(
                        &mut builder,
                        &FbOpenInterestArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            contracts: Some(&contracts),
                            quote_value: quote.as_ref(),
                            change_24h: change.as_ref(),
                            change_pct_24h: change_pct.as_ref(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
                MarketObservation::InstrumentStatus(value) => {
                    let market_id = builder.create_string(&value.market_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let status = builder.create_string(&value.status);
                    let reason = value
                        .reason
                        .as_ref()
                        .map(|value| builder.create_string(value));
                    let source_id = builder.create_string(&value.source_id);
                    instrument_statuses.push(FbInstrumentStatus::create(
                        &mut builder,
                        &FbInstrumentStatusArgs {
                            market_id: Some(market_id),
                            instrument_id: Some(instrument_id),
                            status: Some(status),
                            reason,
                            effective_at_unix_nanos: value
                                .effective_at_unix_nanos
                                .unwrap_or_default(),
                            event_time_unix_nanos: value.observed_at_unix_nanos,
                            source_id: Some(source_id),
                        },
                    ));
                }
            }
        }
        let quotes = builder.create_vector(&quotes);
        let trades = builder.create_vector(&trades);
        let bars = builder.create_vector(&bars);
        let greeks = builder.create_vector(&greeks);
        let rates = builder.create_vector(&rates);
        let ticker_24h = builder.create_vector(&ticker_24h);
        let mark_prices = builder.create_vector(&mark_prices);
        let index_prices = builder.create_vector(&index_prices);
        let funding_rates = builder.create_vector(&funding_rates);
        let open_interests = builder.create_vector(&open_interests);
        let freshness = builder.create_vector(&freshness);
        let instrument_statuses = builder.create_vector(&instrument_statuses);
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
                    .filter(|v| {
                        matches!(
                            v,
                            MarketObservation::Bar(_)
                                | MarketObservation::TradeBar(_)
                                | MarketObservation::QuoteBar(_)
                        )
                    })
                    .count() as u64,
                greeks_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::OptionGreeks(_)))
                    .count() as u64,
                rate_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::Rate(_)))
                    .count() as u64,
                ticker_24h_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::Ticker24h(_)))
                    .count() as u64,
                mark_price_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::MarkPrice(_)))
                    .count() as u64,
                index_price_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::IndexPrice(_)))
                    .count() as u64,
                funding_rate_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::FundingRate(_)))
                    .count() as u64,
                open_interest_count: observations
                    .iter()
                    .filter(|v| matches!(v, MarketObservation::OpenInterest(_)))
                    .count() as u64,
                instrument_statuses: Some(instrument_statuses),
                quotes: Some(quotes),
                trades: Some(trades),
                bars: Some(bars),
                greeks: Some(greeks),
                rates: Some(rates),
                ticker_24h: Some(ticker_24h),
                mark_prices: Some(mark_prices),
                index_prices: Some(index_prices),
                funding_rates: Some(funding_rates),
                open_interests: Some(open_interests),
                freshness: Some(freshness),
            },
        );
        let snapshot_id = builder.create_string(&format!("{}:{}", view_key, snapshot.generation));
        let view_key = builder.create_string(view_key);
        let actor_id = builder.create_string(&self.actor_id);
        let workspace_id = non_empty_string(&mut builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(&mut builder, &self.identity.launch_id);
        let instance_id = non_empty_string(&mut builder, &self.identity.instance_id);
        let generated_at = now_unix_nanos();
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(actor_id),
                workspace_id,
                launch_id,
                instance_id,
                version: 1,
                generation: snapshot.generation,
                generated_at_unix_nanos: generated_at,
                as_of_unix_nanos: market_snapshot_as_of(snapshot),
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
    pub fn publish(&mut self, snapshot: &MarketCurrentView) -> Result<(), String> {
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
                .map(|market| market.exchange_id.as_str())
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
    identity: InstanceIdentity,
}

impl MmapOrderBookSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> std::io::Result<Self> {
        Self::create_with_identity(path, slot_size, actor_id, InstanceIdentity::default())
    }

    pub fn create_with_identity(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> std::io::Result<Self> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size)?,
            actor_id: actor_id.into(),
            identity,
        })
    }

    pub fn publish_books(
        &mut self,
        generation: u64,
        books: &std::collections::BTreeMap<String, OrderBook>,
    ) -> Result<(), String> {
        self.publish_books_with_view_key(generation, books, "market.orderbook", "market")
    }

    pub fn publish_books_with_view_key(
        &mut self,
        generation: u64,
        books: &std::collections::BTreeMap<String, OrderBook>,
        view_key_value: &str,
        source_id_value: &str,
    ) -> Result<(), String> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let mut encoded = Vec::new();
        for book in books.values() {
            let market_id = builder.create_string(&book.market_id);
            let instrument_id = builder.create_string(&book.instrument_id);
            let source_id = builder.create_string(if book.source_id.is_empty() {
                source_id_value
            } else {
                &book.source_id
            });
            let bids = encode_levels(&mut builder, &book.bids)?;
            let asks = encode_levels(&mut builder, &book.asks)?;
            let depth_policy_value = match book.depth_policy {
                crate::model::DepthPolicy::Full => "full".to_owned(),
                crate::model::DepthPolicy::TopN(limit) => format!("top_n:{limit}"),
            };
            let depth_policy = builder.create_string(&depth_policy_value);
            let checksum = book
                .checksum
                .as_ref()
                .map(|value| builder.create_string(value));
            encoded.push(FbOrderBook::create(
                &mut builder,
                &FbOrderBookArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    sequence: book.sequence,
                    event_time_unix_nanos: book.event_time_unix_nanos,
                    source_id: Some(source_id),
                    checksum,
                    depth_policy: Some(depth_policy),
                    first_sequence: book.cursor.first_sequence,
                    last_sequence: book.cursor.last_sequence,
                    synchronized: book.synchronized,
                    bids: Some(bids),
                    asks: Some(asks),
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
        let workspace_id = non_empty_string(&mut builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(&mut builder, &self.identity.launch_id);
        let instance_id = non_empty_string(&mut builder, &self.identity.instance_id);
        let generated_at = now_unix_nanos();
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(actor_id),
                workspace_id,
                launch_id,
                instance_id,
                version: 1,
                generation,
                generated_at_unix_nanos: generated_at,
                as_of_unix_nanos: books
                    .values()
                    .map(|book| book.event_time_unix_nanos)
                    .max()
                    .unwrap_or_default(),
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

fn encode_bar_record<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut flatbuffers::FlatBufferBuilder<'a, A>,
    value: &crate::model::Bar,
    bar_kind_value: &str,
) -> Result<flatbuffers::WIPOffset<FbBar<'a>>, String> {
    let market_id = builder.create_string(&value.market_id);
    let instrument_id = builder.create_string(&value.instrument_id);
    let timeframe = builder.create_string(&value.timeframe);
    let source_id = builder.create_string(&value.source_id);
    let derivation = builder.create_string(&value.derivation);
    let bar_kind = builder.create_string(bar_kind_value);
    let open = decimal64(builder, Some(&value.open))
        .ok_or_else(|| "bar open is not a decimal".to_string())?;
    let high = decimal64(builder, Some(&value.high))
        .ok_or_else(|| "bar high is not a decimal".to_string())?;
    let low = decimal64(builder, Some(&value.low))
        .ok_or_else(|| "bar low is not a decimal".to_string())?;
    let close = decimal64(builder, Some(&value.close))
        .ok_or_else(|| "bar close is not a decimal".to_string())?;
    let volume = decimal64(builder, value.volume.as_deref());
    Ok(FbBar::create(
        builder,
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
            bar_kind: Some(bar_kind),
        },
    ))
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

fn aggressor_side(value: Option<&str>) -> kairos_protocol::generated::kairos::common::v_1::Side {
    use kairos_protocol::generated::kairos::common::v_1::Side;
    match value.map(|value| value.to_ascii_lowercase()).as_deref() {
        Some("buy") => Side::BUY,
        Some("sell") => Side::SELL,
        _ => Side::UNSPECIFIED,
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn market_snapshot_as_of(snapshot: &MarketCurrentView) -> u64 {
    snapshot
        .freshness
        .values()
        .map(|value| value.last_event_time_unix_nanos)
        .chain(
            snapshot
                .order_books
                .values()
                .map(|value| value.event_time_unix_nanos),
        )
        .chain(snapshot.views.values().map(market_observation_time))
        .chain(snapshot.latest.values().map(market_observation_time))
        .max()
        .unwrap_or_default()
}

fn market_observation_time(value: &MarketObservation) -> u64 {
    match value {
        MarketObservation::Quote(value) => value.observed_at_unix_nanos,
        MarketObservation::Trade(value) => value.observed_at_unix_nanos,
        MarketObservation::Bar(value) => value.observed_at_unix_nanos,
        MarketObservation::TradeBar(value) => value.bar.observed_at_unix_nanos,
        MarketObservation::QuoteBar(value) => value.bar.observed_at_unix_nanos,
        MarketObservation::OptionGreeks(value) => value.observed_at_unix_nanos,
        MarketObservation::Rate(value) => value.observed_at_unix_nanos,
        MarketObservation::Ticker24h(value) => value.observed_at_unix_nanos,
        MarketObservation::MarkPrice(value) => value.observed_at_unix_nanos,
        MarketObservation::IndexPrice(value) => value.observed_at_unix_nanos,
        MarketObservation::FundingRate(value) => value.observed_at_unix_nanos,
        MarketObservation::OpenInterest(value) => value.observed_at_unix_nanos,
        MarketObservation::InstrumentStatus(value) => value.observed_at_unix_nanos,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{DataFreshnessStatus, FeedStatus, MarketFreshness};

    #[test]
    fn snapshot_header_carries_generation_and_business_as_of_time() {
        let directory = tempfile::tempdir().unwrap();
        let publisher = MmapMarketSnapshotPublisher::create(
            directory.path().join("market.snapshot"),
            1024 * 1024,
            "market:test",
        )
        .unwrap();
        let snapshot = MarketCurrentView {
            actor_id: "market:test".into(),
            generation: 13,
            freshness: [(
                "source:market:quote".into(),
                MarketFreshness {
                    source_id: "source".into(),
                    market_id: "market".into(),
                    data_kind: "quote".into(),
                    last_event_time_unix_nanos: 987,
                    last_received_time_unix_nanos: 999,
                    status: DataFreshnessStatus::Current,
                },
            )]
            .into(),
            feed_status: FeedStatus::Ready,
            ..Default::default()
        };

        let payload = publisher.encode(&snapshot).unwrap();
        let root =
            kairos_protocol::generated::kairos::market::v_1::root_as_market_data_snapshot(&payload)
                .unwrap();
        let header = root.header();
        assert_eq!(header.version(), 1);
        assert_eq!(header.generation(), 13);
        assert_eq!(header.as_of_unix_nanos(), 987);
        assert!(header.generated_at_unix_nanos() > 0);
        assert!(header.complete());
    }
}
