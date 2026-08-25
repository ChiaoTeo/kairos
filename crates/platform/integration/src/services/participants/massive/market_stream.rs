//! Massive-owned planning and admission policy for market WebSocket streams.

use std::collections::BTreeMap;

use crate::{IntegrationError, MarketDataKind, MarketEvent, MarketEventKind, MarketFeed};

/// Massive documents a 1,000-contract maximum for option quote subscriptions.
const OPTIONS_QUOTE_LIMIT: usize = 1_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Product {
    Stocks,
    Options,
    Futures,
    Indices,
    Forex,
    Crypto,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct PlannedStream(pub(crate) String);

#[derive(Clone, Debug)]
pub(crate) struct MarketStreamPolicy {
    product: Product,
}

impl MarketStreamPolicy {
    pub(crate) const fn new(product: Product) -> Self {
        Self { product }
    }

    pub(crate) fn plan(&self, feed: &MarketFeed) -> Result<PlannedStream, IntegrationError> {
        let symbol = feed.symbol.as_ref().ok_or_else(|| {
            IntegrationError::InvalidRequest(format!(
                "Massive {:?} feed requires symbol",
                feed.kind
            ))
        })?;
        let channel = match (self.product, feed.kind) {
            (Product::Indices, MarketDataKind::IndexPrice) => "V",
            (Product::Indices, MarketDataKind::Bar) => aggregate_channel(feed, "A", "AM")?,
            (Product::Forex, MarketDataKind::Quote) => "C",
            (Product::Forex, MarketDataKind::Bar) => aggregate_channel(feed, "CAS", "CA")?,
            (Product::Crypto, MarketDataKind::Quote) => "XQ",
            (Product::Crypto, MarketDataKind::Trade) => "XT",
            (Product::Crypto, MarketDataKind::Bar) => aggregate_channel(feed, "XAS", "XA")?,
            (Product::Stocks | Product::Options | Product::Futures, MarketDataKind::Quote) => "Q",
            (Product::Stocks | Product::Options | Product::Futures, MarketDataKind::Trade) => "T",
            (
                Product::Stocks | Product::Options | Product::Futures,
                MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar,
            ) => aggregate_channel(feed, "A", "AM")?,
            (_, kind) => {
                return Err(IntegrationError::InvalidRequest(format!(
                    "Massive {:?} WebSocket does not support {kind:?}",
                    self.product
                )));
            },
        };
        Ok(PlannedStream(format!(
            "{channel}.{}",
            symbol.as_str().to_ascii_uppercase()
        )))
    }

    pub(crate) fn admit(
        &self,
        streams: &BTreeMap<PlannedStream, usize>,
    ) -> Result<(), IntegrationError> {
        if self.product == Product::Options {
            let quote_count = streams
                .keys()
                .filter(|stream| stream.0.starts_with("Q."))
                .count();
            if quote_count > OPTIONS_QUOTE_LIMIT {
                return Err(IntegrationError::RateLimited(format!(
                    "Massive options quote capacity is {OPTIONS_QUOTE_LIMIT} contracts per connection; requested {quote_count}"
                )));
            }
        }
        Ok(())
    }
}

fn aggregate_channel<'a>(
    feed: &MarketFeed,
    second: &'a str,
    minute: &'a str,
) -> Result<&'a str, IntegrationError> {
    match feed.interval.as_deref() {
        Some("1s") => Ok(second),
        Some("1m") | None => Ok(minute),
        Some(interval) => Err(IntegrationError::InvalidRequest(format!(
            "Massive live aggregate interval is unsupported: {interval}"
        ))),
    }
}

pub(crate) fn event_is_demanded<'a>(
    feeds: impl IntoIterator<Item = &'a MarketFeed>,
    event: &MarketEvent,
) -> bool {
    feeds.into_iter().any(|feed| {
        feed.symbol
            .as_ref()
            .is_some_and(|symbol| symbol.as_str().eq_ignore_ascii_case(event.symbol.as_str()))
            && matches!(
                (feed.kind, event.kind),
                (MarketDataKind::Quote, MarketEventKind::Quote)
                    | (MarketDataKind::Trade, MarketEventKind::Trade)
                    | (MarketDataKind::Bar, MarketEventKind::Bar)
                    | (MarketDataKind::TradeBar, MarketEventKind::Bar)
                    | (MarketDataKind::QuoteBar, MarketEventKind::Bar)
                    | (MarketDataKind::IndexPrice, MarketEventKind::IndexPrice)
            )
    })
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ParticipantSymbol;

    use super::*;

    fn feed(kind: MarketDataKind, symbol: &str) -> MarketFeed {
        MarketFeed {
            kind,
            symbol: Some(ParticipantSymbol::new(symbol).unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        }
    }

    #[test]
    fn each_product_owns_its_channel_vocabulary() {
        assert_eq!(
            MarketStreamPolicy::new(Product::Indices)
                .plan(&feed(MarketDataKind::IndexPrice, "I:SPX"))
                .unwrap()
                .0,
            "V.I:SPX"
        );
        assert_eq!(
            MarketStreamPolicy::new(Product::Forex)
                .plan(&feed(MarketDataKind::Quote, "C:EURUSD"))
                .unwrap()
                .0,
            "C.C:EURUSD"
        );
        assert_eq!(
            MarketStreamPolicy::new(Product::Crypto)
                .plan(&feed(MarketDataKind::Trade, "X:BTC-USD"))
                .unwrap()
                .0,
            "XT.X:BTC-USD"
        );
    }

    #[test]
    fn option_quote_capacity_is_enforced_before_control_io() {
        let policy = MarketStreamPolicy::new(Product::Options);
        let streams = (0..=OPTIONS_QUOTE_LIMIT)
            .map(|index| (PlannedStream(format!("Q.O:{index}")), 1))
            .collect();
        assert!(matches!(
            policy.admit(&streams),
            Err(IntegrationError::RateLimited(_))
        ));
    }
}
