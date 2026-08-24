use std::collections::BTreeSet;

use kairos_primitives::market::Provider;
use kairos_primitives::reference::{AssetClass, ExchangeId};
use serde::{Deserialize, Serialize};

use super::MarketFeedId;
use crate::domain::market::ProviderSegmentCode;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub(crate) struct FeedDescriptor {
    pub(crate) id: MarketFeedId,
    /// Business provider served by this runtime feed. Replay/derived feeds do
    /// not pretend to be providers and therefore leave this empty.
    pub(crate) provider: Option<Provider>,
    pub(crate) exchange_id: Option<ExchangeId>,
    pub(crate) market_type: Option<ProviderSegmentCode>,
    pub(crate) asset_type: Option<AssetClass>,
    /// Realtime observations implemented by this concrete source adapter.
    #[serde(default)]
    pub(crate) observation_capabilities: BTreeSet<crate::domain::observation::ObservationKind>,
}

impl FeedDescriptor {
    #[cfg(test)]
    pub(crate) fn new(
        id: MarketFeedId,
        exchange_id: ExchangeId,
        market_type: impl Into<String>,
        asset_type: Option<String>,
    ) -> Result<Self, String> {
        Self::build(id, None, exchange_id, market_type, asset_type)
    }

    pub(crate) fn for_provider(
        id: MarketFeedId,
        provider: impl Into<String>,
        exchange_id: ExchangeId,
        market_type: impl Into<String>,
        asset_type: Option<String>,
    ) -> Result<Self, String> {
        let provider = Provider::new(provider.into()).map_err(|error| error.to_string())?;
        Self::build(id, Some(provider), exchange_id, market_type, asset_type)
    }

    fn build(
        id: MarketFeedId,
        provider: Option<Provider>,
        exchange_id: ExchangeId,
        market_type: impl Into<String>,
        asset_type: Option<String>,
    ) -> Result<Self, String> {
        let market_type = ProviderSegmentCode::new(market_type.into().trim().to_ascii_lowercase())
            .map_err(|error| error.to_string())?;
        let asset_type = asset_type
            .map(|value| value.trim().to_ascii_lowercase().parse::<AssetClass>())
            .transpose()
            .map_err(|error| error.to_string())?;
        Ok(Self {
            id,
            provider,
            exchange_id: Some(exchange_id),
            market_type: Some(market_type),
            asset_type,
            observation_capabilities: BTreeSet::new(),
        })
    }

    pub(crate) fn all_routes(id: MarketFeedId) -> Self {
        Self {
            id,
            provider: None,
            exchange_id: None,
            market_type: None,
            asset_type: None,
            observation_capabilities: BTreeSet::new(),
        }
    }

    pub(crate) fn with_observation_capabilities(
        mut self,
        capabilities: impl IntoIterator<Item = crate::domain::observation::ObservationKind>,
    ) -> Self {
        self.observation_capabilities = capabilities.into_iter().collect();
        self
    }
}
