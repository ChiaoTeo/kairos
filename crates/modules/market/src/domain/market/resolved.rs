use kairos_primitives::reference::{
    AssetClass, Exchange, InstrumentId, InstrumentKind, MarketId, ReferenceStatus,
};
use serde::{Deserialize, Serialize};

use super::MarketDataRoute;
use crate::domain::observation::ObservationScope;
use crate::domain::source::SourceId;

/// Canonical market identity joined with one active provider access.
///
/// Composition owns the Reference join. Once this value enters application,
/// every provider-facing fact is available only through `route`.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ResolvedMarket {
    pub scope: ObservationScope,
    pub instrument_id: InstrumentId,
    pub instrument_kind: InstrumentKind,
    pub exchange_id: Option<Exchange>,
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
    pub fn from_reference(
        market_id: MarketId,
        instrument_id: InstrumentId,
        instrument_kind: InstrumentKind,
        exchange_id: Exchange,
        route: MarketDataRoute,
    ) -> Result<Self, String> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        let value = Self {
            scope: ObservationScope::from(market_id),
            instrument_id,
            instrument_kind,
            exchange_id: Some(exchange_id),
            asset_type: None,
            underlying_instrument_id: None,
            route,
            source_id: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

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
            scope: ObservationScope::market(
                MarketId::new(market_id.into())
                    .map_err(|error| error.to_string())?
                    .to_string(),
            )?,
            instrument_id: InstrumentId::new(instrument_id.into())
                .map_err(|error| error.to_string())?,
            instrument_kind,
            exchange_id: Some(Exchange::new(exchange_id).map_err(|error| error.to_string())?),
            asset_type: None,
            underlying_instrument_id: None,
            route,
            source_id: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    /// Resolve a provider route whose observations describe an instrument-wide
    /// or consolidated feed, rather than one canonical exchange market.
    pub fn consolidated(
        instrument_id: impl Into<String>,
        network_id: Option<String>,
        instrument_kind: InstrumentKind,
        route: MarketDataRoute,
    ) -> Result<Self, String> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        let instrument_id =
            InstrumentId::new(instrument_id.into()).map_err(|error| error.to_string())?;
        let value = Self {
            scope: ObservationScope::consolidated(instrument_id.to_string(), network_id)?,
            instrument_id,
            instrument_kind,
            exchange_id: None,
            asset_type: None,
            underlying_instrument_id: None,
            route,
            source_id: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn consolidated_reference(
        instrument_id: InstrumentId,
        network_id: Option<String>,
        instrument_kind: InstrumentKind,
        route: MarketDataRoute,
    ) -> Result<Self, String> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        if network_id
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err("observation network_id must be non-empty when present".into());
        }
        let value = Self {
            scope: ObservationScope::Consolidated {
                instrument_id: instrument_id.clone(),
                network_id,
            },
            instrument_id,
            instrument_kind,
            exchange_id: None,
            asset_type: None,
            underlying_instrument_id: None,
            route,
            source_id: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn market_id(&self) -> Option<&MarketId> {
        self.scope.market_id()
    }

    pub fn member_id(&self) -> String {
        self.scope.key()
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
