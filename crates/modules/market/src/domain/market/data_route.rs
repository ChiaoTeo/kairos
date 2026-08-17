use kairos_primitives::{ProviderId, ProviderProductCode, ProviderSymbol};
use serde::{Deserialize, Serialize};

/// Provider access selected by composition for one canonical market.
///
/// All fields are required: application and source code consume this resolved
/// route and never infer provider facts from a listing symbol or market kind.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketDataRoute {
    pub access_id: String,
    pub provider_id: ProviderId,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
}

impl MarketDataRoute {
    pub fn new(
        access_id: impl Into<String>,
        provider_id: impl Into<String>,
        provider_product: impl Into<String>,
        provider_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        let access_id = access_id.into();
        if access_id.trim().is_empty() {
            return Err("market-data access id is required".into());
        }
        Ok(Self {
            access_id,
            provider_id: ProviderId::new(provider_id.into()).map_err(|error| error.to_string())?,
            provider_product: ProviderProductCode::new(provider_product.into())
                .map_err(|error| error.to_string())?,
            provider_symbol: ProviderSymbol::new(provider_symbol.into())
                .map_err(|error| error.to_string())?,
        })
    }
}
