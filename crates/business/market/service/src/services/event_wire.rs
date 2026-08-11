//! Private FlatBuffers encoding for the Market event stream.
//!
//! Domain events cross the application/runtime boundary as Kairos-owned
//! values. This service owns their concrete wire representation so the
//! application process facade does not depend on generated schema builders.

use crate::domain::events::MarketEvent;
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::{DepthPolicy, OrderBook};
use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, MessageHeader, MessageHeaderArgs,
};
use kairos_protocol::generated::kairos::market::v_1::{
    finish_bar_message_buffer, finish_funding_rate_message_buffer, finish_greeks_message_buffer,
    finish_index_price_message_buffer, finish_instrument_status_message_buffer,
    finish_mark_price_message_buffer, finish_open_interest_message_buffer,
    finish_order_book_message_buffer, finish_quote_message_buffer, finish_rate_message_buffer,
    finish_ticker_24h_message_buffer, finish_trade_message_buffer, Bar as FbBar,
    BarArgs as FbBarArgs, BarMessage, BarMessageArgs, FundingRate as FbFundingRate,
    FundingRateArgs as FbFundingRateArgs, FundingRateMessage, FundingRateMessageArgs,
    Greeks as FbGreeks, GreeksArgs as FbGreeksArgs, GreeksMessage, GreeksMessageArgs,
    IndexPrice as FbIndexPrice, IndexPriceArgs as FbIndexPriceArgs, IndexPriceMessage,
    IndexPriceMessageArgs, InstrumentStatus as FbInstrumentStatus,
    InstrumentStatusArgs as FbInstrumentStatusArgs, InstrumentStatusMessage,
    InstrumentStatusMessageArgs, MarkPrice as FbMarkPrice, MarkPriceArgs as FbMarkPriceArgs,
    MarkPriceMessage, MarkPriceMessageArgs, OpenInterest as FbOpenInterest,
    OpenInterestArgs as FbOpenInterestArgs, OpenInterestMessage, OpenInterestMessageArgs,
    OrderBook as FbOrderBook, OrderBookArgs as FbOrderBookArgs, OrderBookLevel as FbOrderBookLevel,
    OrderBookLevelArgs as FbOrderBookLevelArgs, OrderBookMessage, OrderBookMessageArgs,
    Quote as FbQuote, QuoteArgs as FbQuoteArgs, QuoteMessage, QuoteMessageArgs, Rate as FbRate,
    RateArgs as FbRateArgs, RateMessage, RateMessageArgs, Ticker24h as FbTicker24h,
    Ticker24hArgs as FbTicker24hArgs, Ticker24hMessage, Ticker24hMessageArgs, Trade as FbTrade,
    TradeArgs as FbTradeArgs, TradeMessage, TradeMessageArgs,
};
use kairos_protocol::InstanceIdentity;

macro_rules! finish_market_event {
    ($builder:expr, $header:expr, $payload:expr, $message:ident, $args:ident, $finish:ident) => {{
        let root = $message::create(
            $builder,
            &$args {
                header: Some($header),
                payload: Some($payload),
            },
        );
        $finish($builder, root);
        Ok($builder.finished_data().to_vec())
    }};
}

pub(crate) fn encode_event(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    event: &MarketEvent,
) -> Result<Vec<u8>, String> {
    match event {
        MarketEvent::Observation(observation) => {
            encode_observation_event(actor_id, identity, sequence, observation)
        }
        MarketEvent::OrderBook(book) => encode_orderbook_event(actor_id, identity, sequence, book),
    }
}

pub(crate) fn encode_observation_event(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    observation: &MarketObservation,
) -> Result<Vec<u8>, String> {
    let stream_id = "market.events";
    match observation {
        MarketObservation::Quote(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let instrument_id = builder.create_string(&value.instrument_id);
            let market_id = builder.create_string(&value.market_id);
            let source_id = builder.create_string(&value.source_id);
            let bid_price = value.bid_price.map(decimal64_price);
            let bid_quantity = value.bid_quantity.map(decimal64_quantity);
            let ask_price = value.ask_price.map(decimal64_price);
            let ask_quantity = value.ask_quantity.map(decimal64_quantity);
            let quote = FbQuote::create(
                &mut builder,
                &FbQuoteArgs {
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    bid_price: bid_price.as_ref(),
                    bid_quantity: bid_quantity.as_ref(),
                    ask_price: ask_price.as_ref(),
                    ask_quantity: ask_quantity.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                    ..Default::default()
                },
            );
            let root = QuoteMessage::create(
                &mut builder,
                &QuoteMessageArgs {
                    header: Some(header),
                    payload: Some(quote),
                },
            );
            finish_quote_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Trade(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let instrument_id = builder.create_string(&value.instrument_id);
            let market_id = builder.create_string(&value.market_id);
            let source_id = builder.create_string(&value.source_id);
            let price = decimal64_price(value.price);
            let quantity = decimal64_quantity(value.quantity);
            let cost = value.cost.map(decimal64_money);
            let trade_id = value.trade_id.as_ref().map(|id| builder.create_string(id));
            let trade = FbTrade::create(
                &mut builder,
                &FbTradeArgs {
                    trade_id,
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    price: Some(&price),
                    quantity: Some(&quantity),
                    cost: cost.as_ref(),
                    aggressor_side: aggressor_side(value.aggressor_side.as_deref()),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            let root = TradeMessage::create(
                &mut builder,
                &TradeMessageArgs {
                    header: Some(header),
                    payload: Some(trade),
                },
            );
            finish_trade_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Bar(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let timeframe = builder.create_string(&value.timeframe);
            let source_id = builder.create_string(&value.source_id);
            let derivation = builder.create_string(&value.derivation);
            let bar_kind = builder.create_string(if value.derivation.starts_with("trade:") {
                "trade_bar"
            } else if value.derivation.starts_with("quote:") {
                "quote_bar"
            } else {
                "bar"
            });
            let open = decimal64_price(value.open);
            let high = decimal64_price(value.high);
            let low = decimal64_price(value.low);
            let close = decimal64_price(value.close);
            let volume = value.volume.map(decimal64_quantity);
            let bar = FbBar::create(
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
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                    derivation: Some(derivation),
                    bar_kind: Some(bar_kind),
                },
            );
            let root = BarMessage::create(
                &mut builder,
                &BarMessageArgs {
                    header: Some(header),
                    payload: Some(bar),
                },
            );
            finish_bar_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::TradeBar(value) => {
            let mut bar = value.bar.clone();
            bar.derivation = if bar.derivation.is_empty() {
                "trade".into()
            } else {
                format!("trade:{}", bar.derivation)
            };
            encode_observation_event(actor_id, identity, sequence, &MarketObservation::Bar(bar))
        }
        MarketObservation::QuoteBar(value) => {
            let mut bar = value.bar.clone();
            bar.derivation = if bar.derivation.is_empty() {
                "quote".into()
            } else {
                format!("quote:{}", bar.derivation)
            };
            encode_observation_event(actor_id, identity, sequence, &MarketObservation::Bar(bar))
        }
        MarketObservation::OptionGreeks(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let derivation = builder.create_string(&value.derivation);
            let strike = value.strike.map(decimal64_price);
            let delta = value.delta.map(decimal64_rate);
            let gamma = value.gamma.map(decimal64_rate);
            let vega = value.vega.map(decimal64_rate);
            let theta = value.theta.map(decimal64_rate);
            let implied_volatility = value.implied_volatility.map(decimal64_rate);
            let greeks = FbGreeks::create(
                &mut builder,
                &FbGreeksArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    expiry_unix_nanos: value.expiry_unix_nanos.map_or(0, Into::into),
                    strike: strike.as_ref(),
                    delta: delta.as_ref(),
                    gamma: gamma.as_ref(),
                    vega: vega.as_ref(),
                    theta: theta.as_ref(),
                    implied_volatility: implied_volatility.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                    derivation: Some(derivation),
                },
            );
            let root = GreeksMessage::create(
                &mut builder,
                &GreeksMessageArgs {
                    header: Some(header),
                    payload: Some(greeks),
                },
            );
            finish_greeks_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Rate(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let rate_id = builder.create_string(&value.rate_id);
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let basis = builder.create_string(&value.basis);
            let source_id = builder.create_string(&value.source_id);
            let rate_value = decimal64_rate(value.value);
            let mark_price = value.mark_price.map(decimal64_price);
            let rate = FbRate::create(
                &mut builder,
                &FbRateArgs {
                    rate_id: Some(rate_id),
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    basis: Some(basis),
                    value: Some(&rate_value),
                    mark_price: mark_price.as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            let root = RateMessage::create(
                &mut builder,
                &RateMessageArgs {
                    header: Some(header),
                    payload: Some(rate),
                },
            );
            finish_rate_message_buffer(&mut builder, root);
            Ok(builder.finished_data().to_vec())
        }
        MarketObservation::Ticker24h(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let payload = FbTicker24h::create(
                &mut builder,
                &FbTicker24hArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    last_price: value.last_price.map(decimal64_price).as_ref(),
                    bid_price: value.bid_price.map(decimal64_price).as_ref(),
                    bid_quantity: value.bid_quantity.map(decimal64_quantity).as_ref(),
                    ask_price: value.ask_price.map(decimal64_price).as_ref(),
                    ask_quantity: value.ask_quantity.map(decimal64_quantity).as_ref(),
                    open_price: value.open_price.map(decimal64_price).as_ref(),
                    high_price: value.high_price.map(decimal64_price).as_ref(),
                    low_price: value.low_price.map(decimal64_price).as_ref(),
                    volume_base: value.volume_base.map(decimal64_quantity).as_ref(),
                    volume_quote: value.volume_quote.map(decimal64_money).as_ref(),
                    price_change_abs: value.price_change_abs.map(decimal64_money).as_ref(),
                    price_change_pct: value.price_change_pct.map(decimal64_rate).as_ref(),
                    vwap: value.vwap.map(decimal64_price).as_ref(),
                    mark_price: value.mark_price.map(decimal64_price).as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                Ticker24hMessage,
                Ticker24hMessageArgs,
                finish_ticker_24h_message_buffer
            )
        }
        MarketObservation::MarkPrice(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let mark_price = decimal64_price(value.mark_price);
            let index_price = value.index_price.map(decimal64_price);
            let settlement = value.estimated_settlement_price.map(decimal64_price);
            let funding = value.funding_rate.map(decimal64_rate);
            let payload = FbMarkPrice::create(
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
                        .map_or(0, Into::into),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                MarkPriceMessage,
                MarkPriceMessageArgs,
                finish_mark_price_message_buffer
            )
        }
        MarketObservation::IndexPrice(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let payload = FbIndexPrice::create(
                &mut builder,
                &FbIndexPriceArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    spot_index_price: value.spot_index_price.map(decimal64_price).as_ref(),
                    contract_index_price: value.contract_index_price.map(decimal64_price).as_ref(),
                    index_price: value.index_price.map(decimal64_price).as_ref(),
                    funding_rate: value.funding_rate.map(decimal64_rate).as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                IndexPriceMessage,
                IndexPriceMessageArgs,
                finish_index_price_message_buffer
            )
        }
        MarketObservation::FundingRate(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let rate = decimal64_rate(value.funding_rate);
            let payload = FbFundingRate::create(
                &mut builder,
                &FbFundingRateArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    funding_rate: Some(&rate),
                    funding_period_seconds: value.funding_period_seconds.unwrap_or_default(),
                    next_funding_time_unix_nanos: value
                        .next_funding_time_unix_nanos
                        .map_or(0, Into::into),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                FundingRateMessage,
                FundingRateMessageArgs,
                finish_funding_rate_message_buffer
            )
        }
        MarketObservation::OpenInterest(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let source_id = builder.create_string(&value.source_id);
            let contracts = decimal64_quantity(value.contracts);
            let payload = FbOpenInterest::create(
                &mut builder,
                &FbOpenInterestArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    contracts: Some(&contracts),
                    quote_value: value.quote_value.map(decimal64_money).as_ref(),
                    change_24h: value.change_24h.map(decimal64_money).as_ref(),
                    change_pct_24h: value.change_pct_24h.map(decimal64_rate).as_ref(),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                OpenInterestMessage,
                OpenInterestMessageArgs,
                finish_open_interest_message_buffer
            )
        }
        MarketObservation::InstrumentStatus(value) => {
            let mut builder = FlatBufferBuilder::new();
            let header = event_header(
                &mut builder,
                actor_id,
                identity,
                stream_id,
                sequence,
                value.observed_at_unix_nanos.get(),
            );
            let market_id = builder.create_string(&value.market_id);
            let instrument_id = builder.create_string(&value.instrument_id);
            let status = builder.create_string(value.status.as_str());
            let reason = value
                .reason
                .as_ref()
                .map(|value| builder.create_string(value));
            let source_id = builder.create_string(&value.source_id);
            let payload = FbInstrumentStatus::create(
                &mut builder,
                &FbInstrumentStatusArgs {
                    market_id: Some(market_id),
                    instrument_id: Some(instrument_id),
                    status: Some(status),
                    reason,
                    effective_at_unix_nanos: value.effective_at_unix_nanos.map_or(0, Into::into),
                    event_time_unix_nanos: value.observed_at_unix_nanos.get(),
                    source_id: Some(source_id),
                },
            );
            finish_market_event!(
                &mut builder,
                header,
                payload,
                InstrumentStatusMessage,
                InstrumentStatusMessageArgs,
                finish_instrument_status_message_buffer
            )
        }
    }
}

fn encode_orderbook_event(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    book: &OrderBook,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let header = event_header(
        &mut builder,
        actor_id,
        identity,
        "market.events",
        sequence,
        book.event_time_unix_nanos.get(),
    );
    let market_id = builder.create_string(book.market_id.as_str());
    let instrument_id = builder.create_string(book.instrument_id.as_str());
    let source_id = builder.create_string(&book.source_id);
    let checksum = book
        .checksum
        .as_deref()
        .map(|value| builder.create_string(value));
    let depth_policy_value = match book.depth_policy {
        DepthPolicy::Full => "full".to_owned(),
        DepthPolicy::TopN(limit) => format!("top_n:{limit}"),
    };
    let depth_policy = builder.create_string(&depth_policy_value);
    let bids = encode_orderbook_levels(&mut builder, &book.bids);
    let asks = encode_orderbook_levels(&mut builder, &book.asks);
    let payload = FbOrderBook::create(
        &mut builder,
        &FbOrderBookArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            sequence: book.sequence.get(),
            event_time_unix_nanos: book.event_time_unix_nanos.get(),
            source_id: Some(source_id),
            checksum,
            depth_policy: Some(depth_policy),
            first_sequence: book.cursor.first_sequence.get(),
            last_sequence: book.cursor.last_sequence.get(),
            synchronized: book.synchronized,
            bids: Some(bids),
            asks: Some(asks),
        },
    );
    let root = OrderBookMessage::create(
        &mut builder,
        &OrderBookMessageArgs {
            header: Some(header),
            payload: Some(payload),
        },
    );
    finish_order_book_message_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_orderbook_levels<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    levels: &[crate::domain::orderbook::PriceLevel],
) -> flatbuffers::WIPOffset<
    flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<FbOrderBookLevel<'a>>>,
> {
    let encoded = levels
        .iter()
        .map(|level| {
            let price = decimal64_price(level.price);
            let quantity = decimal64_quantity(level.quantity);
            FbOrderBookLevel::create(
                builder,
                &FbOrderBookLevelArgs {
                    price: Some(&price),
                    quantity: Some(&quantity),
                    order_count: 0,
                },
            )
        })
        .collect::<Vec<_>>();
    builder.create_vector(&encoded)
}

fn event_header<'a, A: flatbuffers::Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    actor_id: &str,
    identity: &InstanceIdentity,
    stream_id: &str,
    sequence: u64,
    event_time: u64,
) -> flatbuffers::WIPOffset<MessageHeader<'a>> {
    let message_id = builder.create_string(&format!("market:{sequence}"));
    let stream = builder.create_string(stream_id);
    let actor = builder.create_string(actor_id);
    let workspace_id = non_empty_string(builder, &identity.workspace_id);
    let launch_id = non_empty_string(builder, &identity.launch_id);
    let instance_id = non_empty_string(builder, &identity.instance_id);
    MessageHeader::create(
        builder,
        &MessageHeaderArgs {
            message_id: Some(message_id),
            stream_id: Some(stream),
            producer_id: Some(actor),
            workspace_id,
            launch_id,
            instance_id,
            sequence,
            event_time_unix_nanos: event_time,
            publish_time_unix_nanos: event_time,
        },
    )
}

fn decimal64_price(value: kairos_domain_types::Price) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn decimal64_quantity(value: kairos_domain_types::Quantity) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn decimal64_money(value: kairos_domain_types::Money) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn decimal64_rate(value: kairos_domain_types::Rate) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn aggressor_side(value: Option<&str>) -> kairos_protocol::generated::kairos::common::v_1::Side {
    use kairos_protocol::generated::kairos::common::v_1::Side;
    match value.map(|value| value.to_ascii_lowercase()).as_deref() {
        Some("buy") => Side::BUY,
        Some("sell") => Side::SELL,
        _ => Side::UNSPECIFIED,
    }
}

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}
