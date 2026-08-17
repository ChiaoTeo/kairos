use std::collections::BTreeSet;

use kairos_primitives::{ProviderId, ProviderProductCode, ProviderSymbol};
use serde::{Deserialize, Serialize};

/// Provider access selected by composition for one canonical market.
///
/// All fields are required: application and source code consume this resolved
/// route and never infer provider facts from a listing symbol or market kind.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketDataRoute {
    pub route_id: String,
    pub provider_id: ProviderId,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
    /// Code-owned adapter capabilities resolved by composition. These are
    /// independent from whether a workspace currently configured a source.
    #[serde(default)]
    pub observation_capabilities: BTreeSet<crate::domain::observation::ObservationKind>,
}

impl MarketDataRoute {
    pub fn new(
        route_id: impl Into<String>,
        provider_id: impl Into<String>,
        provider_product: impl Into<String>,
        provider_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        let route_id = route_id.into();
        if route_id.trim().is_empty() {
            return Err("market-data route id is required".into());
        }
        Ok(Self {
            route_id,
            provider_id: ProviderId::new(provider_id.into()).map_err(|error| error.to_string())?,
            provider_product: ProviderProductCode::new(provider_product.into())
                .map_err(|error| error.to_string())?,
            provider_symbol: ProviderSymbol::new(provider_symbol.into())
                .map_err(|error| error.to_string())?,
            observation_capabilities: BTreeSet::new(),
        })
    }

    pub fn with_observation_capabilities(
        mut self,
        capabilities: impl IntoIterator<Item = crate::domain::observation::ObservationKind>,
    ) -> Self {
        self.observation_capabilities = capabilities.into_iter().collect();
        self
    }
}
