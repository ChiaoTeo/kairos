//! Provider event normalization into Market-owned facts.

use kairos_integration::application::{MarketEvent, MarketEventKind};
use kairos_primitives::Money;

use super::messages::{SourceInput, SourceOrderBookUpdate};
use crate::domain::market::ResolvedMarket;
use crate::domain::observation::order_book::PriceLevel;
use crate::domain::observation::{
    Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks, Quote,
    QuoteBar, Rate, Ticker24h, Trade, TradeBar,
};
use crate::domain::source::{SourceEpoch, SourceId};

pub(super) enum Normalized {
    Observation(MarketObservation),
    OrderBook(SourceOrderBookUpdate),
}

pub(super) fn with_epoch(
    value: Normalized,
    source_id: SourceId,
    epoch: SourceEpoch,
) -> SourceInput {
    match value {
        Normalized::Observation(observation) => SourceInput::Observation {
            source_id,
            epoch,
            observation,
        },
        Normalized::OrderBook(update) => SourceInput::OrderBook {
            source_id,
            epoch,
            update,
        },
    }
}

pub(super) fn normalize(
    source_id: &SourceId,
    market: &ResolvedMarket,
    event: MarketEvent,
) -> Result<Option<Normalized>, String> {
    let source_id = source_id.to_string();
    let observation = match event.kind {
        MarketEventKind::Quote | MarketEventKind::Snapshot => MarketObservation::Quote(Quote {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            bid_price: event.price,
            bid_quantity: event.quantity,
            ask_price: event.ask_price,
            ask_quantity: event.ask_quantity,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Trade => MarketObservation::Trade(Trade {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            trade_id: None,
            price: event.price.ok_or("trade event has no price")?,
            quantity: event.quantity.ok_or("trade event has no quantity")?,
            cost: None,
            aggressor_side: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Bar | MarketEventKind::TradeBar | MarketEventKind::QuoteBar => {
            let value = event.bar.ok_or("bar event has no bar payload")?;
            let bar = Bar {
                market_id: market.market_id.clone(),
                instrument_id: market.instrument_id.clone(),
                timeframe: value.timeframe,
                open: value.open,
                high: value.high,
                low: value.low,
                close: value.close,
                volume: value.volume,
                observed_at_unix_nanos: event.observed_at_unix_nanos,
                source_id,
                derivation: value.derivation,
            };
            match event.kind {
                MarketEventKind::TradeBar => MarketObservation::TradeBar(TradeBar { bar }),
                MarketEventKind::QuoteBar => MarketObservation::QuoteBar(QuoteBar { bar }),
                _ => MarketObservation::Bar(bar),
            }
        }
        MarketEventKind::Greeks => {
            let value = event.greeks.ok_or("greeks event has no greeks payload")?;
            MarketObservation::OptionGreeks(OptionGreeks {
                market_id: market.market_id.clone(),
                instrument_id: market.instrument_id.clone(),
                expiry_unix_nanos: value.expiry_unix_nanos,
                strike: value.strike,
                delta: value.delta,
                gamma: value.gamma,
                vega: value.vega,
                theta: value.theta,
                implied_volatility: value.implied_volatility,
                observed_at_unix_nanos: event.observed_at_unix_nanos,
                source_id,
                derivation: value.derivation,
            })
        }
        MarketEventKind::Rate => MarketObservation::Rate(Rate {
            rate_id: format!("funding:{}", market.market_id),
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            basis: "funding".into(),
            value: event.rate.ok_or("rate event has no value")?,
            mark_price: event.ask_price,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::Ticker24h => MarketObservation::Ticker24h(Ticker24h {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            last_price: event.price,
            bid_price: None,
            bid_quantity: None,
            ask_price: event.ask_price,
            ask_quantity: event.ask_quantity,
            open_price: None,
            high_price: None,
            low_price: None,
            volume_base: event.quantity,
            volume_quote: None,
            price_change_abs: None,
            price_change_pct: None,
            vwap: None,
            mark_price: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::MarkPrice => MarketObservation::MarkPrice(MarkPrice {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            mark_price: event.price.ok_or("mark price event has no price")?,
            index_price: event.ask_price,
            estimated_settlement_price: None,
            funding_rate: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::IndexPrice => MarketObservation::IndexPrice(IndexPrice {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            spot_index_price: event.price,
            contract_index_price: None,
            index_price: event.price,
            funding_rate: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::FundingRate => MarketObservation::FundingRate(FundingRate {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            funding_rate: event.rate.ok_or("funding rate event has no value")?,
            funding_period_seconds: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::OpenInterest => MarketObservation::OpenInterest(OpenInterest {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            contracts: event
                .quantity
                .ok_or("open interest event has no quantity")?,
            quote_value: event
                .price
                .map(|value| Money::new(value.mantissa(), value.scale()))
                .transpose()
                .map_err(|error| error.to_string())?,
            change_24h: None,
            change_pct_24h: None,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id,
        }),
        MarketEventKind::InstrumentStatus => {
            return Err("InstrumentStatus is not part of Market v2".into());
        }
        MarketEventKind::BookSnapshot | MarketEventKind::BookDelta => {
            let update = SourceOrderBookUpdate {
                market: Box::new(market.clone()),
                source_id,
                market_id: market.market_id.clone(),
                instrument_id: market.instrument_id.clone(),
                first_sequence: event
                    .first_sequence
                    .ok_or("order book event has no first sequence")?,
                last_sequence: event
                    .last_sequence
                    .or(event.sequence)
                    .ok_or("order book event has no last sequence")?,
                event_time_unix_nanos: event.observed_at_unix_nanos,
                bids: event
                    .bids
                    .into_iter()
                    .map(|(price, quantity)| PriceLevel { price, quantity })
                    .collect(),
                asks: event
                    .asks
                    .into_iter()
                    .map(|(price, quantity)| PriceLevel { price, quantity })
                    .collect(),
                snapshot: event.kind == MarketEventKind::BookSnapshot,
            };
            return Ok(Some(Normalized::OrderBook(update)));
        }
        MarketEventKind::Heartbeat => return Ok(None),
    };
    Ok(Some(Normalized::Observation(observation)))
}
