use kairos_primitives::{AssetClass, Exchange, ProviderProductCode};
use serde::{Deserialize, Serialize};

use super::SourceId;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct SourceRouteKey {
    pub source_id: Option<String>,
    pub exchange: String,
    pub market_type: ProviderProductCode,
    pub asset_type: Option<AssetClass>,
}

impl SourceRouteKey {
    pub fn from_market(market: &crate::domain::market::ResolvedMarket) -> Self {
        Self {
            source_id: market.source_id.as_ref().map(ToString::to_string),
            exchange: market.exchange_id.to_string().to_ascii_lowercase(),
            market_type: market.route.provider_product.clone(),
            asset_type: market.asset_type,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceDescriptor {
    pub id: SourceId,
    pub exchange_id: Option<Exchange>,
    pub market_type: Option<ProviderProductCode>,
    pub asset_type: Option<AssetClass>,
}

impl SourceDescriptor {
    pub fn new(
        id: SourceId,
        exchange_id: Exchange,
        market_type: impl Into<String>,
        asset_type: Option<String>,
    ) -> Result<Self, String> {
        let market_type = ProviderProductCode::new(market_type.into().trim().to_ascii_lowercase())
            .map_err(|error| error.to_string())?;
        let asset_type = asset_type
            .map(|value| value.trim().to_ascii_lowercase().parse::<AssetClass>())
            .transpose()
            .map_err(|error| error.to_string())?;
        Ok(Self {
            id,
            exchange_id: Some(exchange_id),
            market_type: Some(market_type),
            asset_type,
        })
    }

    pub fn all_routes(id: SourceId) -> Self {
        Self {
            id,
            exchange_id: None,
            market_type: None,
            asset_type: None,
        }
    }
}
