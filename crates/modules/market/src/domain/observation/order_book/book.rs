use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::time::{Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use super::{OrderBookError, PriceLevel};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum DepthPolicy {
    #[default]
    Full,
    TopN(u32),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub(crate) struct DepthCursor {
    pub first_sequence: Sequence,
    pub last_sequence: Sequence,
    pub checksum: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderBook {
    pub provider: Provider,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub sequence: Sequence,
    pub event_time_unix_nanos: UnixNanos,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub synchronized: bool,
    pub depth_policy: DepthPolicy,
    pub(crate) cursor: DepthCursor,
    pub checksum: Option<String>,
}

impl OrderBook {
    pub fn with_depth_policy(mut self, policy: DepthPolicy) -> Result<Self, OrderBookError> {
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
    ) -> Result<Self, OrderBookError> {
        Self::snapshot_with_provider(
            Provider::new("market").expect("canonical market provider is valid"),
            market_id,
            instrument_id,
            sequence,
            event_time_unix_nanos,
            bids,
            asks,
        )
    }

    pub fn snapshot_with_provider(
        provider: Provider,
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        sequence: impl Into<Sequence>,
        event_time_unix_nanos: impl Into<UnixNanos>,
        bids: Vec<PriceLevel>,
        asks: Vec<PriceLevel>,
    ) -> Result<Self, OrderBookError> {
        let sequence = sequence.into();
        let market_id =
            MarketId::new(market_id.into()).map_err(|source| OrderBookError::InvalidSemantic {
                identity: "market id",
                source,
            })?;
        let instrument_id = InstrumentId::new(instrument_id.into()).map_err(|source| {
            OrderBookError::InvalidSemantic {
                identity: "instrument id",
                source,
            }
        })?;
        let mut value = Self {
            provider,
            market_id,
            instrument_id,
            sequence,
            event_time_unix_nanos: event_time_unix_nanos.into(),
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
        format!("{}:{}", self.provider, self.market_id)
    }

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

    pub(super) fn validate(&self) -> Result<(), OrderBookError> {
        if self.provider.trim().is_empty()
            || self.market_id.trim().is_empty()
            || self.instrument_id.trim().is_empty()
        {
            return Err(OrderBookError::IdentityRequired);
        }
        for level in self.bids.iter().chain(self.asks.iter()) {
            if level.quantity.is_zero() {
                return Err(OrderBookError::LevelQuantityRequired);
            }
        }
        Ok(())
    }

    pub(super) fn apply_depth_policy(&mut self) {
        if let DepthPolicy::TopN(limit) = self.depth_policy {
            self.bids.truncate(limit as usize);
            self.asks.truncate(limit as usize);
        }
    }
}
