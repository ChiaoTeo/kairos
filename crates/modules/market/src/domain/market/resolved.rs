use kairos_primitives::{
    AssetClass, Exchange, InstrumentId, InstrumentKind, MarketId, ReferenceStatus,
};
use serde::{Deserialize, Serialize};

use super::MarketDataRoute;
use crate::domain::source::SourceId;

/// Canonical market identity joined with one active provider access.
///
/// Composition owns the Reference join. Once this value enters application,
/// every provider-facing fact is available only through `route`.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ResolvedMarket {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub instrument_kind: InstrumentKind,
    pub exchange_id: Exchange,
    #[serde(default)]
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    pub route: MarketDataRoute,
    /// Optional Market-owned runtime source constraint.
    #[serde(default)]
    pub source_id: Option<SourceId>,
    pub status: ReferenceStatus,
}

impl ResolvedMarket {
    pub fn new(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        instrument_kind: InstrumentKind,
        exchange_id: impl Into<String>,
        route: MarketDataRoute,
    ) -> Result<Self, String> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        let value = Self {
            market_id: MarketId::new(market_id.into()).map_err(|error| error.to_string())?,
            instrument_id: InstrumentId::new(instrument_id.into())
                .map_err(|error| error.to_string())?,
            instrument_kind,
            exchange_id: Exchange::new(exchange_id).map_err(|error| error.to_string())?,
            asset_type: None,
            underlying_instrument_id: None,
            route,
            source_id: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn with_source(mut self, source_id: impl Into<String>) -> Result<Self, String> {
        self.source_id = Some(SourceId::new(source_id)?);
        self.validate()?;
        Ok(self)
    }

    pub fn with_asset_type(mut self, asset_type: impl Into<String>) -> Result<Self, String> {
        self.asset_type = Some(
            asset_type
                .into()
                .parse::<AssetClass>()
                .map_err(|error| error.to_string())?,
        );
        self.validate()?;
        Ok(self)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        if self.status == ReferenceStatus::Unknown {
            return Err("status is required".into());
        }
        Ok(())
    }

    pub fn is_active(&self) -> bool {
        matches!(
            self.status,
            ReferenceStatus::Active | ReferenceStatus::Trading
        )
    }
}
