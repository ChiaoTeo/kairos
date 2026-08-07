use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketDescriptor {
    pub market_id: String,
    pub instrument_id: String,
    pub venue_id: String,
    pub market_type: String,
    pub source_symbol: String,
    pub status: String,
}

impl MarketDescriptor {
    pub fn new(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        venue_id: impl Into<String>,
        market_type: impl Into<String>,
        source_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        let value = Self {
            market_id: market_id.into(),
            instrument_id: instrument_id.into(),
            venue_id: venue_id.into(),
            market_type: market_type.into(),
            source_symbol: source_symbol.into(),
            status: "active".into(),
        };
        value.validate()?;
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), String> {
        for (name, value) in [
            ("market_id", &self.market_id),
            ("instrument_id", &self.instrument_id),
            ("venue_id", &self.venue_id),
            ("market_type", &self.market_type),
            ("source_symbol", &self.source_symbol),
            ("status", &self.status),
        ] {
            if value.trim().is_empty() {
                return Err(format!("{name} is required"));
            }
        }
        Ok(())
    }

    pub fn is_active(&self) -> bool {
        matches!(self.status.as_str(), "active" | "trading")
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSelectionQuery {
    pub market_id: Option<String>,
    pub venue_id: Option<String>,
    pub market_type: Option<String>,
    pub source_symbol: Option<String>,
    pub active_only: bool,
}

impl MarketSelectionQuery {
    pub fn matches(&self, market: &MarketDescriptor) -> bool {
        if self
            .market_id
            .as_deref()
            .is_some_and(|v| v != market.market_id)
            || self
                .venue_id
                .as_deref()
                .is_some_and(|v| v != market.venue_id)
            || self
                .market_type
                .as_deref()
                .is_some_and(|v| v != market.market_type)
            || self
                .source_symbol
                .as_deref()
                .is_some_and(|v| !v.eq_ignore_ascii_case(&market.source_symbol))
        {
            return false;
        }
        !self.active_only || market.is_active()
    }
}
