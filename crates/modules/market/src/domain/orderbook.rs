use kairos_primitives::{InstrumentId, MarketId, Price, Quantity, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PriceLevel {
    pub price: Price,
    pub quantity: Quantity,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum DepthPolicy {
    #[default]
    Full,
    TopN(u32),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct DepthCursor {
    pub first_sequence: Sequence,
    pub last_sequence: Sequence,
    pub checksum: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderBook {
    pub source_id: String,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub sequence: Sequence,
    pub event_time_unix_nanos: UnixNanos,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub synchronized: bool,
    pub depth_policy: DepthPolicy,
    pub cursor: DepthCursor,
    pub checksum: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderBookDelta {
    pub source_id: String,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub first_sequence: Sequence,
    pub last_sequence: Sequence,
    pub event_time_unix_nanos: UnixNanos,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub checksum: Option<String>,
}

impl OrderBook {
    pub fn with_depth_policy(mut self, policy: DepthPolicy) -> Result<Self, String> {
        self.depth_policy = policy;
        self.apply_depth_policy();
        self.validate()?;
        self.checksum = Some(self.canonical_checksum());
        self.cursor.checksum = self.checksum.clone();
        Ok(self)
    }

    pub fn snapshot(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        sequence: impl Into<Sequence>,
        event_time_unix_nanos: impl Into<UnixNanos>,
        bids: Vec<PriceLevel>,
        asks: Vec<PriceLevel>,
    ) -> Result<Self, String> {
        Self::snapshot_with_source(
            "market",
            market_id,
            instrument_id,
            sequence,
            event_time_unix_nanos,
            bids,
            asks,
        )
    }

    pub fn snapshot_with_source(
        source_id: impl Into<String>,
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        sequence: impl Into<Sequence>,
        event_time_unix_nanos: impl Into<UnixNanos>,
        bids: Vec<PriceLevel>,
        asks: Vec<PriceLevel>,
    ) -> Result<Self, String> {
        let sequence = sequence.into();
        let event_time_unix_nanos = event_time_unix_nanos.into();
        let market_id = market_id.into();
        let instrument_id = instrument_id.into();
        let mut value = Self {
            source_id: source_id.into(),
            market_id: MarketId::new(market_id)
                .map_err(|error| format!("invalid market id: {error}"))?,
            instrument_id: InstrumentId::new(instrument_id)
                .map_err(|error| format!("invalid instrument id: {error}"))?,
            sequence,
            event_time_unix_nanos,
            bids,
            asks,
            synchronized: true,
            depth_policy: DepthPolicy::Full,
            cursor: DepthCursor {
                first_sequence: sequence,
                last_sequence: sequence,
                checksum: None,
            },
            checksum: None,
        };
        value.validate()?;
        value.checksum = Some(value.canonical_checksum());
        value.cursor.checksum = value.checksum.clone();
        Ok(value)
    }

    pub fn key(&self) -> String {
        format!("{}:{}", self.source_id, self.market_id)
    }

    /// Canonical local checksum used by replay/tests and by providers whose
    /// checksum algorithm is the canonical price-level concatenation.
    pub fn canonical_checksum(&self) -> String {
        let mut value = String::new();
        for level in &self.bids {
            value.push_str(&level.price.to_string());
            value.push(':');
            value.push_str(&level.quantity.to_string());
            value.push('|');
        }
        value.push(';');
        for level in &self.asks {
            value.push_str(&level.price.to_string());
            value.push(':');
            value.push_str(&level.quantity.to_string());
            value.push('|');
        }
        value
    }

    pub fn apply_delta(&mut self, delta: OrderBookDelta) -> Result<(), String> {
        if !self.synchronized {
            return Err("order book is not synchronized; snapshot is required".into());
        }
        if delta.source_id != self.source_id
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
        apply_levels(&mut self.bids, delta.bids);
        apply_levels(&mut self.asks, delta.asks);
        self.apply_depth_policy();
        self.sequence = delta.last_sequence;
        self.cursor.last_sequence = delta.last_sequence;
        self.checksum = delta.checksum.or_else(|| Some(self.canonical_checksum()));
        self.cursor.checksum = self.checksum.clone();
        self.event_time_unix_nanos = delta.event_time_unix_nanos;
        Ok(())
    }

    fn validate(&self) -> Result<(), String> {
        if self.source_id.trim().is_empty()
            || self.market_id.trim().is_empty()
            || self.instrument_id.trim().is_empty()
        {
            return Err("order book identity is required".into());
        }
        for level in self.bids.iter().chain(self.asks.iter()) {
            if level.quantity.is_zero() {
                return Err("order book level requires price and quantity".into());
            }
        }
        Ok(())
    }

    fn apply_depth_policy(&mut self) {
        if let DepthPolicy::TopN(limit) = self.depth_policy {
            self.bids.truncate(limit as usize);
            self.asks.truncate(limit as usize);
        }
    }
}

fn apply_levels(levels: &mut Vec<PriceLevel>, updates: Vec<PriceLevel>) {
    for update in updates {
        if let Some(existing) = levels.iter_mut().find(|level| level.price == update.price) {
            existing.quantity = update.quantity;
        } else if !update.quantity.is_zero() {
            levels.push(update);
        }
    }
    levels.retain(|level| !level.quantity.is_zero());
}

#[cfg(test)]
mod tests {
    use kairos_primitives::{Price, Quantity};
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
                source_id: "market".into(),
                market_id: kairos_primitives::MarketId::new("BTC-USD").unwrap(),
                instrument_id: kairos_primitives::InstrumentId::new("BTC-USD").unwrap(),
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
