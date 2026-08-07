use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PriceLevel {
    pub price: String,
    pub quantity: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderBook {
    pub market_id: String,
    pub instrument_id: String,
    pub sequence: u64,
    pub event_time_unix_nanos: u64,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub synchronized: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderBookDelta {
    pub market_id: String,
    pub instrument_id: String,
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub event_time_unix_nanos: u64,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
}

impl OrderBook {
    pub fn snapshot(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        sequence: u64,
        event_time_unix_nanos: u64,
        bids: Vec<PriceLevel>,
        asks: Vec<PriceLevel>,
    ) -> Result<Self, String> {
        let value = Self {
            market_id: market_id.into(),
            instrument_id: instrument_id.into(),
            sequence,
            event_time_unix_nanos,
            bids,
            asks,
            synchronized: true,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn apply_delta(&mut self, delta: OrderBookDelta) -> Result<(), String> {
        if !self.synchronized {
            return Err("order book is not synchronized; snapshot is required".into());
        }
        if delta.market_id != self.market_id || delta.instrument_id != self.instrument_id {
            return Err("order book delta identity does not match snapshot".into());
        }
        if delta.first_sequence != self.sequence + 1 {
            self.synchronized = false;
            return Err(format!(
                "order book sequence gap: expected {}, got {}",
                self.sequence + 1,
                delta.first_sequence
            ));
        }
        if delta.last_sequence < delta.first_sequence {
            return Err("order book delta has invalid sequence range".into());
        }
        apply_levels(&mut self.bids, delta.bids);
        apply_levels(&mut self.asks, delta.asks);
        self.sequence = delta.last_sequence;
        self.event_time_unix_nanos = delta.event_time_unix_nanos;
        Ok(())
    }

    fn validate(&self) -> Result<(), String> {
        if self.market_id.trim().is_empty() || self.instrument_id.trim().is_empty() {
            return Err("order book identity is required".into());
        }
        for level in self.bids.iter().chain(self.asks.iter()) {
            if level.price.trim().is_empty() || level.quantity.trim().is_empty() {
                return Err("order book level requires price and quantity".into());
            }
        }
        Ok(())
    }
}

fn apply_levels(levels: &mut Vec<PriceLevel>, updates: Vec<PriceLevel>) {
    for update in updates {
        if let Some(existing) = levels.iter_mut().find(|level| level.price == update.price) {
            if update.quantity == "0" {
                existing.quantity.clear();
            } else {
                existing.quantity = update.quantity;
            }
        } else if update.quantity != "0" {
            levels.push(update);
        }
    }
    levels.retain(|level| !level.quantity.is_empty() && level.quantity != "0");
}
