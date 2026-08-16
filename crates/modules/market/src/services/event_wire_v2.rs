//! Typed Market v2 event encoding.
//!
//! This module is deliberately kept at the composition/service boundary:
//! domain observations are mapped field-by-field into the contract-owned
//! FlatBuffers roots.  No JSON round trip is used as a model adapter.

use crate::domain::events::{MarketEvent, OrderBookResyncRequired};
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::{DepthPolicy, OrderBook, OrderBookDelta, PriceLevel};
use flatbuffers::FlatBufferBuilder;
use kairos_market_contract::{event_metadata, EncodeContext};
use kairos_protocol::generated::kairos::common::v_2::{Decimal64, Side};
use kairos_protocol::generated::kairos::market::v_2 as fb;
use kairos_protocol::InstanceIdentity;

pub(crate) fn encode_event(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    event: &MarketEvent,
) -> Result<Vec<u8>, String> {
    match event {
        MarketEvent::Observation(value) => encode_observation(actor_id, identity, sequence, value),
        MarketEvent::OrderBookSnapshot(book) => {
            encode_orderbook_snapshot(actor_id, identity, sequence, book)
        }
        MarketEvent::OrderBookDelta(delta) => {
            encode_orderbook_delta(actor_id, identity, sequence, delta)
        }
        MarketEvent::OrderBookResyncRequired(value) => {
            encode_orderbook_resync(actor_id, identity, sequence, value)
        }
    }
}

fn context(actor_id: &str, identity: &InstanceIdentity, sequence: u64) -> EncodeContext {
    EncodeContext::event(
        actor_id,
        identity.clone(),
        sequence,
        format!("market:{sequence}"),
    )
}

fn dec<T: DecimalValue>(value: T) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

trait DecimalValue {
    fn mantissa(&self) -> i64;
    fn scale(&self) -> u8;
}

macro_rules! decimal_value {
    ($($ty:path),+ $(,)?) => {$ (
        impl DecimalValue for $ty {
            fn mantissa(&self) -> i64 { <$ty>::mantissa(*self) }
            fn scale(&self) -> u8 { <$ty>::scale(*self) }
        }
    )+ };
}

decimal_value!(
    kairos_primitives::Price,
    kairos_primitives::Quantity,
    kairos_primitives::Money,
    kairos_primitives::Rate,
);

fn strings<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    market_id: &str,
    instrument_id: &str,
    source_id: &str,
) -> (
    flatbuffers::WIPOffset<&'a str>,
    flatbuffers::WIPOffset<&'a str>,
    flatbuffers::WIPOffset<&'a str>,
) {
    (
        builder.create_string(market_id),
        builder.create_string(instrument_id),
        builder.create_string(source_id),
    )
}

fn finish_event<'a, T, A, F>(
    builder: &mut FlatBufferBuilder<'a, A>,
    metadata: flatbuffers::WIPOffset<
        kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a>,
    >,
    payload: flatbuffers::WIPOffset<T>,
    root: F,
) -> Vec<u8>
where
    A: flatbuffers::Allocator + 'a,
    F: FnOnce(
        &mut FlatBufferBuilder<'a, A>,
        flatbuffers::WIPOffset<kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a>>,
        flatbuffers::WIPOffset<T>,
    ),
{
    root(builder, metadata, payload);
    builder.finished_data().to_vec()
}

fn encode_observation(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &MarketObservation,
) -> Result<Vec<u8>, String> {
    match value {
        MarketObservation::Quote(value) => {
            let mut b = FlatBufferBuilder::new();
            let metadata = event_metadata(
                &mut b,
                &context(actor_id, identity, sequence),
                value.observed_at_unix_nanos.get(),
            );
            let (market_id, instrument_id, source_id) = strings(
                &mut b,
                &value.market_id,
                &value.instrument_id,
                &value.source_id,
            );
            let bid_price = value.bid_price.map(dec);
            let bid_quantity = value.bid_quantity.map(dec);
            let ask_price = value.ask_price.map(dec);
            let ask_quantity = value.ask_quantity.map(dec);
            let payload = fb::Quote::create(
                &mut b,
                &fb::QuoteArgs {
                    quote_id: None,
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    source_id: Some(source_id),
                    bid_price: bid_price.as_ref(),
                    bid_quantity: bid_quantity.as_ref(),
                    ask_price: ask_price.as_ref(),
                    ask_quantity: ask_quantity.as_ref(),
                    source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                    received_at_unix_nanos: 0,
                },
            );
            Ok(finish_event(
                &mut b,
                metadata,
                payload,
                |b, metadata, payload| {
                    let root = fb::QuoteUpdated::create(
                        b,
                        &fb::QuoteUpdatedArgs {
                            metadata: Some(metadata),
                            quote: Some(payload),
                        },
                    );
                    fb::finish_quote_updated_buffer(b, root);
                },
            ))
        }
        MarketObservation::Trade(value) => {
            let mut b = FlatBufferBuilder::new();
            let metadata = event_metadata(
                &mut b,
                &context(actor_id, identity, sequence),
                value.observed_at_unix_nanos.get(),
            );
            let (market_id, instrument_id, source_id) = strings(
                &mut b,
                &value.market_id,
                &value.instrument_id,
                &value.source_id,
            );
            let price = dec(value.price);
            let quantity = dec(value.quantity);
            let trade_id = value.trade_id.as_deref().map(|v| b.create_string(v));
            let payload = fb::Trade::create(
                &mut b,
                &fb::TradeArgs {
                    trade_id,
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    source_id: Some(source_id),
                    price: Some(&price),
                    quantity: Some(&quantity),
                    aggressor_side: side(value.aggressor_side.as_deref()),
                    source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                    received_at_unix_nanos: 0,
                },
            );
            Ok(finish_event(
                &mut b,
                metadata,
                payload,
                |b, metadata, payload| {
                    let root = fb::TradeOccurred::create(
                        b,
                        &fb::TradeOccurredArgs {
                            metadata: Some(metadata),
                            trade: Some(payload),
                        },
                    );
                    fb::finish_trade_occurred_buffer(b, root);
                },
            ))
        }
        MarketObservation::Bar(value)
        | MarketObservation::TradeBar(crate::domain::observations::TradeBar { bar: value })
        | MarketObservation::QuoteBar(crate::domain::observations::QuoteBar { bar: value }) => {
            let mut b = FlatBufferBuilder::new();
            let metadata = event_metadata(
                &mut b,
                &context(actor_id, identity, sequence),
                value.observed_at_unix_nanos.get(),
            );
            let (market_id, instrument_id, source_id) = strings(
                &mut b,
                &value.market_id,
                &value.instrument_id,
                &value.source_id,
            );
            let spec = b.create_string(&value.timeframe);
            let derivation = value.derivation.as_str();
            let open = dec(value.open);
            let high = dec(value.high);
            let low = dec(value.low);
            let close = dec(value.close);
            let volume = value.volume.map(dec);
            let payload = fb::Bar::create(
                &mut b,
                &fb::BarArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    source_id: Some(source_id),
                    bar_spec_id: Some(spec),
                    kind: bar_kind(derivation),
                    window_start_unix_nanos: 0,
                    window_end_unix_nanos: value.observed_at_unix_nanos.get(),
                    open: Some(&open),
                    high: Some(&high),
                    low: Some(&low),
                    close: Some(&close),
                    volume: volume.as_ref(),
                    source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                    received_at_unix_nanos: 0,
                },
            );
            Ok(finish_event(
                &mut b,
                metadata,
                payload,
                |b, metadata, payload| {
                    let root = fb::BarCompleted::create(
                        b,
                        &fb::BarCompletedArgs {
                            metadata: Some(metadata),
                            bar: Some(payload),
                        },
                    );
                    fb::finish_bar_completed_buffer(b, root);
                },
            ))
        }
        MarketObservation::OptionGreeks(value) => {
            let mut b = FlatBufferBuilder::new();
            let metadata = event_metadata(
                &mut b,
                &context(actor_id, identity, sequence),
                value.observed_at_unix_nanos.get(),
            );
            let (market_id, instrument_id, source_id) = strings(
                &mut b,
                &value.market_id,
                &value.instrument_id,
                &value.source_id,
            );
            let strike = value.strike.map(dec);
            let delta = value.delta.map(dec);
            let gamma = value.gamma.map(dec);
            let vega = value.vega.map(dec);
            let theta = value.theta.map(dec);
            let iv = value.implied_volatility.map(dec);
            let derivation = b.create_string(&value.derivation);
            let payload = fb::Greeks::create(
                &mut b,
                &fb::GreeksArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    source_id: Some(source_id),
                    expiry_unix_nanos: value.expiry_unix_nanos.map(|v| v.get()),
                    strike: strike.as_ref(),
                    delta: delta.as_ref(),
                    gamma: gamma.as_ref(),
                    vega: vega.as_ref(),
                    theta: theta.as_ref(),
                    implied_volatility: iv.as_ref(),
                    source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
                    received_at_unix_nanos: 0,
                    derivation_id: Some(derivation),
                },
            );
            Ok(finish_event(
                &mut b,
                metadata,
                payload,
                |b, metadata, payload| {
                    let root = fb::GreeksUpdated::create(
                        b,
                        &fb::GreeksUpdatedArgs {
                            metadata: Some(metadata),
                            greeks: Some(payload),
                        },
                    );
                    fb::finish_greeks_updated_buffer(b, root);
                },
            ))
        }
        MarketObservation::Rate(value) => encode_rate(actor_id, identity, sequence, value),
        MarketObservation::Ticker24h(value) => encode_ticker(actor_id, identity, sequence, value),
        MarketObservation::MarkPrice(value) => {
            encode_mark_price(actor_id, identity, sequence, value)
        }
        MarketObservation::FundingRate(value) => {
            encode_funding(actor_id, identity, sequence, value)
        }
        MarketObservation::OpenInterest(value) => {
            encode_open_interest(actor_id, identity, sequence, value)
        }
        MarketObservation::IndexPrice(value) => {
            encode_index_price(actor_id, identity, sequence, value)
        }
    }
}

fn side(value: Option<&str>) -> Side {
    match value.map(str::to_ascii_lowercase).as_deref() {
        Some("buy") => Side::BUY,
        Some("sell") => Side::SELL,
        _ => Side::UNSPECIFIED,
    }
}
fn bar_kind(value: &str) -> fb::BarKind {
    if value.eq_ignore_ascii_case("trade") || value.eq_ignore_ascii_case("trades") {
        fb::BarKind::TRADES
    } else if value.eq_ignore_ascii_case("quote") || value.eq_ignore_ascii_case("quotes") {
        fb::BarKind::QUOTES
    } else {
        fb::BarKind::UNSPECIFIED
    }
}

fn encode_rate(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &crate::Rate,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        value.observed_at_unix_nanos.get(),
    );
    let (market_id, instrument_id, source_id) = strings(
        &mut b,
        &value.market_id,
        &value.instrument_id,
        &value.source_id,
    );
    let rate_id = b.create_string(&value.rate_id);
    let basis = b.create_string(&value.basis);
    let rate = dec(value.value);
    let mark = value.mark_price.map(dec);
    let payload = fb::Rate::create(
        &mut b,
        &fb::RateArgs {
            rate_id: Some(rate_id),
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            basis: Some(basis),
            value: Some(&rate),
            mark_price: mark.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::RateUpdated::create(
                b,
                &fb::RateUpdatedArgs {
                    metadata: Some(metadata),
                    rate: Some(payload),
                },
            );
            fb::finish_rate_updated_buffer(b, root);
        },
    ))
}

fn encode_ticker(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &crate::Ticker24h,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        value.observed_at_unix_nanos.get(),
    );
    let (market_id, instrument_id, source_id) = strings(
        &mut b,
        &value.market_id,
        &value.instrument_id,
        &value.source_id,
    );
    let lp = value.last_price.map(dec);
    let bp = value.bid_price.map(dec);
    let bq = value.bid_quantity.map(dec);
    let ap = value.ask_price.map(dec);
    let aq = value.ask_quantity.map(dec);
    let op = value.open_price.map(dec);
    let hi = value.high_price.map(dec);
    let lo = value.low_price.map(dec);
    let vb = value.volume_base.map(dec);
    let vq = value.volume_quote.map(dec);
    let ca = value.price_change_abs.map(dec);
    let cp = value.price_change_pct.map(dec);
    let vw = value.vwap.map(dec);
    let mp = value.mark_price.map(dec);
    let payload = fb::Ticker24h::create(
        &mut b,
        &fb::Ticker24hArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            last_price: lp.as_ref(),
            bid_price: bp.as_ref(),
            bid_quantity: bq.as_ref(),
            ask_price: ap.as_ref(),
            ask_quantity: aq.as_ref(),
            open_price: op.as_ref(),
            high_price: hi.as_ref(),
            low_price: lo.as_ref(),
            volume_base: vb.as_ref(),
            volume_quote: vq.as_ref(),
            price_change_abs: ca.as_ref(),
            price_change_pct: cp.as_ref(),
            vwap: vw.as_ref(),
            mark_price: mp.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::Ticker24hUpdated::create(
                b,
                &fb::Ticker24hUpdatedArgs {
                    metadata: Some(metadata),
                    ticker: Some(payload),
                },
            );
            fb::finish_ticker_24h_updated_buffer(b, root);
        },
    ))
}

fn encode_mark_price(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &crate::MarkPrice,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        value.observed_at_unix_nanos.get(),
    );
    let (market_id, instrument_id, source_id) = strings(
        &mut b,
        &value.market_id,
        &value.instrument_id,
        &value.source_id,
    );
    let mark = dec(value.mark_price);
    let index = value.index_price.map(dec);
    let settlement = value.estimated_settlement_price.map(dec);
    let funding = value.funding_rate.map(dec);
    let payload = fb::MarkPrice::create(
        &mut b,
        &fb::MarkPriceArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            mark_price: Some(&mark),
            index_price: index.as_ref(),
            estimated_settlement_price: settlement.as_ref(),
            funding_rate: funding.as_ref(),
            next_funding_time_unix_nanos: value.next_funding_time_unix_nanos.map_or(0, |v| v.get()),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::MarkPriceUpdated::create(
                b,
                &fb::MarkPriceUpdatedArgs {
                    metadata: Some(metadata),
                    mark_price: Some(payload),
                },
            );
            fb::finish_mark_price_updated_buffer(b, root);
        },
    ))
}

fn encode_funding(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &crate::FundingRate,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        value.observed_at_unix_nanos.get(),
    );
    let (market_id, instrument_id, source_id) = strings(
        &mut b,
        &value.market_id,
        &value.instrument_id,
        &value.source_id,
    );
    let rate = dec(value.funding_rate);
    let payload = fb::FundingRate::create(
        &mut b,
        &fb::FundingRateArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            funding_rate: Some(&rate),
            funding_period_seconds: value.funding_period_seconds.unwrap_or_default(),
            next_funding_time_unix_nanos: value.next_funding_time_unix_nanos.map_or(0, |v| v.get()),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::FundingRateUpdated::create(
                b,
                &fb::FundingRateUpdatedArgs {
                    metadata: Some(metadata),
                    funding_rate: Some(payload),
                },
            );
            fb::finish_funding_rate_updated_buffer(b, root);
        },
    ))
}

fn encode_open_interest(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &crate::OpenInterest,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        value.observed_at_unix_nanos.get(),
    );
    let (market_id, instrument_id, source_id) = strings(
        &mut b,
        &value.market_id,
        &value.instrument_id,
        &value.source_id,
    );
    let contracts = dec(value.contracts);
    let q = value.quote_value.map(dec);
    let c = value.change_24h.map(dec);
    let p = value.change_pct_24h.map(dec);
    let payload = fb::OpenInterest::create(
        &mut b,
        &fb::OpenInterestArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            contracts: Some(&contracts),
            quote_value: q.as_ref(),
            change_24h: c.as_ref(),
            change_pct_24h: p.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::OpenInterestUpdated::create(
                b,
                &fb::OpenInterestUpdatedArgs {
                    metadata: Some(metadata),
                    open_interest: Some(payload),
                },
            );
            fb::finish_open_interest_updated_buffer(b, root);
        },
    ))
}

fn encode_index_price(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &crate::IndexPrice,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        value.observed_at_unix_nanos.get(),
    );
    let (market_id, instrument_id, source_id) = strings(
        &mut b,
        &value.market_id,
        &value.instrument_id,
        &value.source_id,
    );
    let spot = value.spot_index_price.map(dec);
    let contract = value.contract_index_price.map(dec);
    let index = value.index_price.map(dec);
    let funding = value.funding_rate.map(dec);
    let payload = fb::IndexPrice::create(
        &mut b,
        &fb::IndexPriceArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            source_id: Some(source_id),
            spot_index_price: spot.as_ref(),
            contract_index_price: contract.as_ref(),
            index_price: index.as_ref(),
            funding_rate: funding.as_ref(),
            source_observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
            received_at_unix_nanos: 0,
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::IndexPriceUpdated::create(
                b,
                &fb::IndexPriceUpdatedArgs {
                    metadata: Some(metadata),
                    index_price: Some(payload),
                },
            );
            fb::finish_index_price_updated_buffer(b, root);
        },
    ))
}

fn orderbook_identity<'a, A: flatbuffers::Allocator + 'a>(
    b: &mut FlatBufferBuilder<'a, A>,
    book: &OrderBook,
) -> flatbuffers::WIPOffset<fb::OrderBookIdentity<'a>> {
    let s = b.create_string(&book.source_id);
    let m = b.create_string(book.market_id.as_str());
    let i = b.create_string(book.instrument_id.as_str());
    fb::OrderBookIdentity::create(
        b,
        &fb::OrderBookIdentityArgs {
            source_id: Some(s),
            market_id: Some(m),
            instrument_id: Some(i),
        },
    )
}
fn encode_orderbook_snapshot(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    book: &OrderBook,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        book.event_time_unix_nanos.get(),
    );
    let identity_offset = orderbook_identity(&mut b, book);
    let depth = b.create_string(match book.depth_policy {
        DepthPolicy::Full => "full",
        DepthPolicy::TopN(n) => {
            return encode_orderbook_snapshot_top(actor_id, identity, sequence, book, n)
        }
    });
    let bids = levels(&mut b, &book.bids, fb::OrderBookSide::BID);
    let asks = levels(&mut b, &book.asks, fb::OrderBookSide::ASK);
    let checksum = book.checksum.as_deref().map(|v| b.create_string(v));
    let payload = fb::OrderBookSnapshotValue::create(
        &mut b,
        &fb::OrderBookSnapshotValueArgs {
            identity: Some(identity_offset),
            sequence: book.sequence.get(),
            source_observed_at_unix_nanos: book.event_time_unix_nanos.get(),
            received_at_unix_nanos: 0,
            checksum,
            depth_policy: Some(depth),
            bids: Some(bids),
            asks: Some(asks),
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::OrderBookSnapshotReceived::create(
                b,
                &fb::OrderBookSnapshotReceivedArgs {
                    metadata: Some(metadata),
                    snapshot: Some(payload),
                },
            );
            fb::finish_order_book_snapshot_received_buffer(b, root);
        },
    ))
}
fn encode_orderbook_snapshot_top(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    book: &OrderBook,
    n: u32,
) -> Result<Vec<u8>, String> {
    let mut copy = book.clone();
    copy.bids.truncate(n as usize);
    copy.asks.truncate(n as usize);
    encode_orderbook_snapshot(actor_id, identity, sequence, &copy)
}
fn levels<'a, A: flatbuffers::Allocator + 'a>(
    b: &mut FlatBufferBuilder<'a, A>,
    levels: &[PriceLevel],
    side: fb::OrderBookSide,
) -> flatbuffers::WIPOffset<
    flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<fb::OrderBookLevel<'a>>>,
> {
    let offsets = levels
        .iter()
        .map(|level| {
            let p = dec(level.price);
            let q = dec(level.quantity);
            fb::OrderBookLevel::create(
                b,
                &fb::OrderBookLevelArgs {
                    price: Some(&p),
                    quantity: Some(&q),
                    order_count: 0,
                },
            )
        })
        .collect::<Vec<_>>();
    let _ = side;
    b.create_vector(&offsets)
}
fn encode_orderbook_delta(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    delta: &OrderBookDelta,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(
        &mut b,
        &context(actor_id, identity, sequence),
        delta.event_time_unix_nanos.get(),
    );
    let s = b.create_string(&delta.source_id);
    let m = b.create_string(delta.market_id.as_str());
    let i = b.create_string(delta.instrument_id.as_str());
    let identity_offset = fb::OrderBookIdentity::create(
        &mut b,
        &fb::OrderBookIdentityArgs {
            source_id: Some(s),
            market_id: Some(m),
            instrument_id: Some(i),
        },
    );
    let checksum = delta.checksum.as_deref().map(|v| b.create_string(v));
    let mut changes = Vec::new();
    for (side, levels) in [
        (fb::OrderBookSide::BID, &delta.bids),
        (fb::OrderBookSide::ASK, &delta.asks),
    ] {
        for level in levels {
            let p = dec(level.price);
            let q = dec(level.quantity);
            let action = if level.quantity.mantissa() == 0 {
                fb::OrderBookLevelAction::DELETE
            } else {
                fb::OrderBookLevelAction::UPSERT
            };
            changes.push(fb::OrderBookLevelChange::create(
                &mut b,
                &fb::OrderBookLevelChangeArgs {
                    side,
                    action,
                    price: Some(&p),
                    quantity: if action == fb::OrderBookLevelAction::DELETE {
                        None
                    } else {
                        Some(&q)
                    },
                    order_count: 0,
                },
            ));
        }
    }
    let changes = b.create_vector(&changes);
    let payload = fb::OrderBookDeltaValue::create(
        &mut b,
        &fb::OrderBookDeltaValueArgs {
            identity: Some(identity_offset),
            first_sequence: delta.first_sequence.get(),
            last_sequence: delta.last_sequence.get(),
            source_observed_at_unix_nanos: delta.event_time_unix_nanos.get(),
            received_at_unix_nanos: 0,
            checksum,
            changes: Some(changes),
        },
    );
    Ok(finish_event(
        &mut b,
        metadata,
        payload,
        |b, metadata, payload| {
            let root = fb::OrderBookDeltaReceived::create(
                b,
                &fb::OrderBookDeltaReceivedArgs {
                    metadata: Some(metadata),
                    delta: Some(payload),
                },
            );
            fb::finish_order_book_delta_received_buffer(b, root);
        },
    ))
}

fn encode_orderbook_resync(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    value: &OrderBookResyncRequired,
) -> Result<Vec<u8>, String> {
    let mut b = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut b, &context(actor_id, identity, sequence), 0);
    let source_id = b.create_string(&value.source_id);
    let market_id = b.create_string(value.market_id.as_str());
    let instrument_id = b.create_string(value.instrument_id.as_str());
    let identity_offset = fb::OrderBookIdentity::create(
        &mut b,
        &fb::OrderBookIdentityArgs {
            source_id: Some(source_id),
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
        },
    );
    let reason = b.create_string(&value.reason);
    let payload = fb::OrderBookResyncRequired::create(
        &mut b,
        &fb::OrderBookResyncRequiredArgs {
            metadata: Some(metadata),
            identity: Some(identity_offset),
            expected_sequence: value.expected_sequence.get(),
            observed_sequence: value.observed_sequence.get(),
            reason: Some(reason),
        },
    );
    fb::finish_order_book_resync_required_buffer(&mut b, payload);
    Ok(b.finished_data().to_vec())
}

#[cfg(test)]
mod tests {
    use super::encode_event;
    use crate::domain::events::MarketEvent;
    use crate::domain::observations::{Quote, Rate};
    use kairos_primitives::{
        InstrumentId, MarketId, Price, Quantity, Rate as FixedRate, UnixNanos,
    };
    use kairos_market_contract::event::decode_event;
    use kairos_protocol::InstanceIdentity;

    #[test]
    fn quote_event_is_a_v2_root() {
        let quote = Quote {
            market_id: MarketId::new("market:btc").unwrap(),
            instrument_id: InstrumentId::new("instrument:btc").unwrap(),
            bid_price: Some("1".parse::<Price>().unwrap()),
            bid_quantity: Some("2".parse::<Quantity>().unwrap()),
            ask_price: None,
            ask_quantity: None,
            observed_at_unix_nanos: UnixNanos::new(7),
            source_id: "source".into(),
        };
        let bytes = encode_event(
            "market",
            &InstanceIdentity::default(),
            1,
            &MarketEvent::Observation(crate::domain::observations::MarketObservation::Quote(quote)),
        )
        .unwrap();
        assert!(decode_event(&bytes).is_ok());
        assert_eq!(&bytes[4..8], b"MQU2");
    }

    #[test]
    fn rate_event_is_a_v2_root() {
        let rate = Rate {
            rate_id: "funding".into(),
            market_id: MarketId::new("market:btc").unwrap(),
            instrument_id: InstrumentId::new("instrument:btc").unwrap(),
            basis: "annualized".into(),
            value: "0.01".parse::<FixedRate>().unwrap(),
            mark_price: None,
            observed_at_unix_nanos: UnixNanos::new(7),
            source_id: "source".into(),
        };
        let bytes = encode_event(
            "market",
            &InstanceIdentity::default(),
            1,
            &MarketEvent::Observation(crate::domain::observations::MarketObservation::Rate(rate)),
        )
        .unwrap();
        assert!(decode_event(&bytes).is_ok());
        assert_eq!(&bytes[4..8], b"MRU2");
    }
}
