use kairos_primitives::time::Sequence;

use super::{OrderBook, OrderBookDelta, PriceLevel};

impl OrderBook {
    pub fn apply_delta(&mut self, delta: OrderBookDelta) -> Result<(), String> {
        self.apply_delta_ref(&delta)
    }

    pub(crate) fn apply_delta_ref(&mut self, delta: &OrderBookDelta) -> Result<(), String> {
        if !self.synchronized {
            return Err("order book is not synchronized; snapshot is required".into());
        }
        if delta.provider != self.provider
            || delta.market_id != self.market_id
            || delta.instrument_id != self.instrument_id
        {
            return Err("order book delta identity does not match snapshot".into());
        }
        if delta.last_sequence < delta.first_sequence {
            return Err("order book delta has invalid sequence range".into());
        }
        let expected = Sequence::new(self.sequence.get().saturating_add(1));
        if delta.first_sequence > expected {
            self.synchronized = false;
            return Err(format!(
                "order book sequence gap: expected {}, got {}",
                expected, delta.first_sequence
            ));
        }
        if delta.last_sequence < expected {
            return Err(format!(
                "stale order book delta: expected through {}, got {}",
                expected, delta.last_sequence
            ));
        }
        apply_levels(&mut self.bids, &delta.bids);
        apply_levels(&mut self.asks, &delta.asks);
        self.apply_depth_policy();
        self.sequence = delta.last_sequence;
        self.cursor.last_sequence = delta.last_sequence;
        self.checksum = delta
            .checksum
            .clone()
            .or_else(|| Some(self.canonical_checksum()));
        self.cursor.checksum = self.checksum.clone();
        self.event_time_unix_nanos = delta.event_time_unix_nanos;
        Ok(())
    }
}

fn apply_levels(levels: &mut Vec<PriceLevel>, updates: &[PriceLevel]) {
    for update in updates {
        if let Some(existing) = levels.iter_mut().find(|level| level.price == update.price) {
            existing.quantity = update.quantity;
        } else if !update.quantity.is_zero() {
            levels.push(update.clone());
        }
    }
    levels.retain(|level| !level.quantity.is_zero());
}

#[cfg(test)]
mod tests {
    use kairos_primitives::decimal::{Price, Quantity};
    use proptest::prelude::*;

    use super::{OrderBook, OrderBookDelta, PriceLevel};

    proptest! {
        #[test]
        fn applying_a_contiguous_delta_keeps_the_book_synchronized(
            initial_quantity in 1_i64..1_000_000,
            updated_quantity in 1_i64..1_000_000,
        ) {
            let price = Price::new(100, 0).unwrap();
            let initial = PriceLevel {
                price,
                quantity: Quantity::positive(initial_quantity, 0).unwrap(),
            };
            let mut book = OrderBook::snapshot("BTC-USD", "BTC-USD", 10_u64, 1_u64, vec![initial], vec![]).unwrap();
            let delta = OrderBookDelta {
                provider: kairos_primitives::market::Provider::new("market").unwrap(),
                market_id: kairos_primitives::reference::MarketId::new("BTC-USD").unwrap(),
                instrument_id: kairos_primitives::reference::InstrumentId::new("BTC-USD").unwrap(),
                first_sequence: 11_u64.into(),
                last_sequence: 11_u64.into(),
                event_time_unix_nanos: 2_u64.into(),
                bids: vec![PriceLevel {
                    price,
                    quantity: Quantity::positive(updated_quantity, 0).unwrap(),
                }],
                asks: vec![],
                checksum: None,
            };

            prop_assert!(book.apply_delta(delta).is_ok());
            prop_assert!(book.synchronized);
            prop_assert_eq!(book.sequence, 11_u64.into());
            prop_assert_eq!(book.bids.len(), 1);
        }
    }
}
