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
        venue: kairos_conflux::MarketVenueEvidence::default(),
    }
}
