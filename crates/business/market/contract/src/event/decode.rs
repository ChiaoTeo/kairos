use kairos_protocol::generated::kairos::market::v_2 as fb;

use crate::{ContractError, ContractResult, MarketEvent};

pub fn decode_event(bytes: &[u8]) -> ContractResult<MarketEvent<'_>> {
    if fb::quote_updated_buffer_has_identifier(bytes) {
        return fb::root_as_quote_updated(bytes)
            .map(MarketEvent::QuoteUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::trade_occurred_buffer_has_identifier(bytes) {
        return fb::root_as_trade_occurred(bytes)
            .map(MarketEvent::TradeOccurred)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::bar_completed_buffer_has_identifier(bytes) {
        return fb::root_as_bar_completed(bytes)
            .map(MarketEvent::BarCompleted)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::greeks_updated_buffer_has_identifier(bytes) {
        return fb::root_as_greeks_updated(bytes)
            .map(MarketEvent::GreeksUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::rate_updated_buffer_has_identifier(bytes) {
        return fb::root_as_rate_updated(bytes)
            .map(MarketEvent::RateUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::ticker_24h_updated_buffer_has_identifier(bytes) {
        return fb::root_as_ticker_24h_updated(bytes)
            .map(MarketEvent::Ticker24hUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::mark_price_updated_buffer_has_identifier(bytes) {
        return fb::root_as_mark_price_updated(bytes)
            .map(MarketEvent::MarkPriceUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::funding_rate_updated_buffer_has_identifier(bytes) {
        return fb::root_as_funding_rate_updated(bytes)
            .map(MarketEvent::FundingRateUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::open_interest_updated_buffer_has_identifier(bytes) {
        return fb::root_as_open_interest_updated(bytes)
            .map(MarketEvent::OpenInterestUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::index_price_updated_buffer_has_identifier(bytes) {
        return fb::root_as_index_price_updated(bytes)
            .map(MarketEvent::IndexPriceUpdated)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::order_book_snapshot_received_buffer_has_identifier(bytes) {
        return fb::root_as_order_book_snapshot_received(bytes)
            .map(MarketEvent::OrderBookSnapshotReceived)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::order_book_delta_received_buffer_has_identifier(bytes) {
        return fb::root_as_order_book_delta_received(bytes)
            .map(MarketEvent::OrderBookDeltaReceived)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    if fb::order_book_resync_required_buffer_has_identifier(bytes) {
        return fb::root_as_order_book_resync_required(bytes)
            .map(MarketEvent::OrderBookResyncRequired)
            .map_err(|error| ContractError::Invalid(error.to_string()));
    }
    Err(ContractError::Invalid(
        "unknown Market v2 event identifier".into(),
    ))
}
