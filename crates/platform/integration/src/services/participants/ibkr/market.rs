use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ibapi::contracts::Contract;
use ibapi::contracts::tick_types::TickType;
use ibapi::market_data::realtime::TickTypes;
use kairos_primitives::decimal::{Price, Quantity};
use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::time::UnixNanos;

use super::execution::SessionService;
use crate::{IntegrationError, MarketQuote};

const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(10);

/// Reusable TWS snapshot-query mechanics. The participant connection owns the
/// public capability; this service only adapts the upstream library protocol.
pub(crate) struct MarketQueryService {
    pub(crate) session: Arc<SessionService>,
    pub(crate) exchange: String,
    pub(crate) currency: String,
}

impl MarketQueryService {
    pub(crate) async fn quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        let client = self.session.client().await?;
        let mut quotes = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let contract = Contract::stock(symbol.as_str())
                .on_exchange(&self.exchange)
                .in_currency(&self.currency)
                .build();
            let ticks = client
                .market_data(&contract)
                .snapshot_once(SNAPSHOT_TIMEOUT)
                .await
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            quotes.push(normalize_quote(symbol, &ticks)?);
        }
        Ok(quotes)
    }
}

fn normalize_quote(
    symbol: &ParticipantSymbol,
    ticks: &[TickTypes],
) -> Result<MarketQuote, IntegrationError> {
    let mut quote = MarketQuote {
        symbol: symbol.clone(),
        bid_price: None,
        bid_quantity: None,
        ask_price: None,
        ask_quantity: None,
        last_price: None,
        observed_at_unix_nanos: now(),
    };
    for tick in ticks {
        apply_tick(&mut quote, tick)?;
    }
    Ok(quote)
}

pub(crate) fn apply_tick(
    quote: &mut MarketQuote,
    tick: &TickTypes,
) -> Result<bool, IntegrationError> {
    match tick {
        TickTypes::Price(value) => {
            let before = (quote.bid_price, quote.ask_price, quote.last_price);
            set_price(quote, &value.tick_type, value.price)?;
            Ok(before != (quote.bid_price, quote.ask_price, quote.last_price))
        },
        TickTypes::Size(value) => {
            let before = (quote.bid_quantity, quote.ask_quantity);
            set_size(quote, &value.tick_type, value.size)?;
            Ok(before != (quote.bid_quantity, quote.ask_quantity))
        },
        TickTypes::PriceSize(value) => {
            let before = (
                quote.bid_price,
                quote.bid_quantity,
                quote.ask_price,
                quote.ask_quantity,
                quote.last_price,
            );
            set_price(quote, &value.price_tick_type, value.price)?;
            set_size(quote, &value.size_tick_type, value.size)?;
            Ok(before
                != (
                    quote.bid_price,
                    quote.bid_quantity,
                    quote.ask_price,
                    quote.ask_quantity,
                    quote.last_price,
                ))
        },
        _ => Ok(false),
    }
}

pub(crate) fn empty_quote(symbol: ParticipantSymbol) -> MarketQuote {
    MarketQuote {
        symbol,
        bid_price: None,
        bid_quantity: None,
        ask_price: None,
        ask_quantity: None,
        last_price: None,
        observed_at_unix_nanos: now(),
    }
}

fn set_price(quote: &mut MarketQuote, kind: &TickType, value: f64) -> Result<(), IntegrationError> {
    let value = positive_price(value)?;
    match kind {
        TickType::Bid | TickType::DelayedBid => quote.bid_price = value,
        TickType::Ask | TickType::DelayedAsk => quote.ask_price = value,
        TickType::Last | TickType::DelayedLast | TickType::Close => quote.last_price = value,
        _ => {},
    }
    Ok(())
}

fn set_size(quote: &mut MarketQuote, kind: &TickType, value: f64) -> Result<(), IntegrationError> {
    let value = nonnegative_quantity(value)?;
    match kind {
        TickType::BidSize | TickType::DelayedBidSize => quote.bid_quantity = value,
        TickType::AskSize | TickType::DelayedAskSize => quote.ask_quantity = value,
        _ => {},
    }
    Ok(())
}

fn positive_price(value: f64) -> Result<Option<Price>, IntegrationError> {
    if !value.is_finite() || value <= 0.0 {
        return Ok(None);
    }
    Price::new((value * 100_000_000.0).round() as i64, 8)
        .map(Some)
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn nonnegative_quantity(value: f64) -> Result<Option<Quantity>, IntegrationError> {
    if !value.is_finite() || value < 0.0 {
        return Ok(None);
    }
    Quantity::new((value * 100_000_000.0).round() as i64, 8)
        .map(Some)
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn now() -> UnixNanos {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}
