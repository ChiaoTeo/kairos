use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ibapi::contracts::{tick_types::TickType, Contract};
use ibapi::market_data::realtime::TickTypes;
use kairos_primitives::{ParticipantSymbol, Price, Quantity, UnixNanos};

use crate::{IntegrationError, MarketQuote};

use super::execution::SessionService;

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
        match tick {
            TickTypes::Price(value) => set_price(&mut quote, &value.tick_type, value.price)?,
            TickTypes::Size(value) => set_size(&mut quote, &value.tick_type, value.size)?,
            TickTypes::PriceSize(value) => {
                set_price(&mut quote, &value.price_tick_type, value.price)?;
                set_size(&mut quote, &value.size_tick_type, value.size)?;
            }
            _ => {}
        }
    }
    Ok(quote)
}

fn set_price(quote: &mut MarketQuote, kind: &TickType, value: f64) -> Result<(), IntegrationError> {
    let value = positive_price(value)?;
    match kind {
        TickType::Bid | TickType::DelayedBid => quote.bid_price = value,
        TickType::Ask | TickType::DelayedAsk => quote.ask_price = value,
        TickType::Last | TickType::DelayedLast | TickType::Close => quote.last_price = value,
        _ => {}
    }
    Ok(())
}

fn set_size(quote: &mut MarketQuote, kind: &TickType, value: f64) -> Result<(), IntegrationError> {
    let value = nonnegative_quantity(value)?;
    match kind {
        TickType::BidSize | TickType::DelayedBidSize => quote.bid_quantity = value,
        TickType::AskSize | TickType::DelayedAskSize => quote.ask_quantity = value,
        _ => {}
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
