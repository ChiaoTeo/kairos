use kairos_primitives::reference::{InstrumentId, MarketId};
use serde::{Deserialize, Serialize};

use super::ObservationIdentityError;

/// Business identity of one observation. Consolidated observations are
/// instrument-scoped and never borrow a listing or provider as a fake venue.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ObservationScope {
    Market {
        market_id: MarketId,
    },
    Consolidated {
        instrument_id: InstrumentId,
        #[serde(default)]
        network_id: Option<String>,
    },
}

impl ObservationScope {
    pub fn market(market_id: impl Into<String>) -> Result<Self, ObservationIdentityError> {
        Ok(Self::Market {
            market_id: MarketId::new(market_id.into())?,
        })
    }

    pub fn consolidated(
        instrument_id: impl Into<String>,
        network_id: Option<String>,
    ) -> Result<Self, ObservationIdentityError> {
        if network_id
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(ObservationIdentityError::EmptyNetworkId);
        }
        Ok(Self::Consolidated {
            instrument_id: InstrumentId::new(instrument_id.into())?,
            network_id,
        })
    }

    pub fn market_id(&self) -> Option<&MarketId> {
        match self {
            Self::Market { market_id } => Some(market_id),
            Self::Consolidated { .. } => None,
        }
    }

    pub fn instrument_id(&self) -> Option<&InstrumentId> {
        match self {
            Self::Market { .. } => None,
            Self::Consolidated { instrument_id, .. } => Some(instrument_id),
        }
    }

    pub fn key(&self) -> String {
        match self {
            Self::Market { market_id } => market_id.to_string(),
            Self::Consolidated {
                instrument_id,
                network_id,
            } => format!(
                "consolidated:{}:{}",
                instrument_id.as_str(),
                network_id.as_deref().unwrap_or("*")
            ),
        }
    }
}

impl From<MarketId> for ObservationScope {
    fn from(market_id: MarketId) -> Self {
        Self::Market { market_id }
    }
}
