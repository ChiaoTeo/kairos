//! Mapping used by Conflux-driven snapshot polling.

use kairos_conflux::{MarketEvent, MarketEventKind};

pub(crate) fn quote_event(quote: kairos_conflux::MarketQuote) -> MarketEvent {
    MarketEvent {
        symbol: quote.symbol,
        kind: MarketEventKind::Quote,
        price: quote.bid_price.or(quote.last_price),
        quantity: quote.bid_quantity,
        rate: None,
        ask_price: quote.ask_price,
        ask_quantity: quote.ask_quantity,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: quote.observed_at_unix_nanos,
        venue: quote.venue,
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn snapshot_quote_preserves_both_sides_without_inventing_trade_evidence() {
        let venue = kairos_conflux::MarketVenueEvidence {
            bid_exchange: Some("19".into()),
            ask_exchange: Some("11".into()),
            tape: Some(3),
            ..Default::default()
        };
        let quote = kairos_conflux::MarketQuote {
            symbol: kairos_primitives::integration::ParticipantSymbol::new("AAPL").unwrap(),
            bid_price: None,
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            last_price: None,
            observed_at_unix_nanos: 7.into(),
            venue: venue.clone(),
        };
        let event = super::quote_event(quote);
        assert_eq!(event.venue, venue);
        assert!(event.venue.trade_exchange.is_none());
    }
}
