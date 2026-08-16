use kairos_primitives::{
    AssetClass, Exchange, InstrumentId, MarketId, ProviderId, ProviderProductCode, ProviderSymbol,
    ReferenceStatus, Symbol,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketDescriptor {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub exchange_id: Exchange,
    pub market_type: ProviderProductCode,
    #[serde(default)]
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    pub source_symbol: Symbol,
    /// Explicit Reference-owned market-data route. Never infer this from the
    /// listing symbol when a provider access is unavailable.
    #[serde(default)]
    pub market_data_access_id: Option<String>,
    #[serde(default)]
    pub provider_symbol: Option<ProviderSymbol>,
    #[serde(default)]
    pub market_data_provider_id: Option<ProviderId>,
    #[serde(default)]
    pub market_data_provider_product: Option<ProviderProductCode>,
    /// Optional market-data source requested by the caller. Reference owns
    /// the canonical market; this field is a route constraint, not provider
    /// payload or exchange identity.
    #[serde(default)]
    pub source_id: Option<String>,
    pub status: ReferenceStatus,
}

impl MarketDescriptor {
    pub fn new(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        exchange_id: impl Into<String>,
        market_type: impl Into<String>,
        source_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        let value = Self {
            market_id: MarketId::new(market_id.into()).map_err(|error| error.to_string())?,
            instrument_id: InstrumentId::new(instrument_id.into())
                .map_err(|error| error.to_string())?,
            exchange_id: Exchange::new(exchange_id).map_err(|error| error.to_string())?,
            market_type: ProviderProductCode::new(market_type.into())
                .map_err(|error| error.to_string())?,
            asset_type: None,
            underlying_instrument_id: None,
            source_symbol: Symbol::new(source_symbol).map_err(|error| error.to_string())?,
            market_data_access_id: None,
            provider_symbol: None,
            market_data_provider_id: None,
            market_data_provider_product: None,
            source_id: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn with_source(mut self, source_id: impl Into<String>) -> Result<Self, String> {
        let source_id = source_id.into();
        if source_id.trim().is_empty() {
            return Err("market source id cannot be blank".into());
        }
        self.source_id = Some(source_id);
        self.validate()?;
        Ok(self)
    }

    pub fn new_with_asset_type(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        exchange_id: impl Into<String>,
        market_type: impl Into<String>,
        asset_type: impl Into<String>,
        source_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        let asset_type = asset_type
            .into()
            .parse::<AssetClass>()
            .map_err(|error| error.to_string())?;
        let mut value = Self::new(
            market_id,
            instrument_id,
            exchange_id,
            market_type,
            source_symbol,
        )?;
        value.asset_type = Some(asset_type);
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.status == ReferenceStatus::Unknown {
            return Err("status is required".into());
        }
        if self
            .source_id
            .as_deref()
            .is_some_and(|source_id| source_id.trim().is_empty())
        {
            return Err("market source id cannot be blank".into());
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

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSelectionQuery {
    pub market_id: Option<MarketId>,
    pub exchange_id: Option<Exchange>,
    pub market_type: Option<ProviderProductCode>,
    pub asset_type: Option<AssetClass>,
    pub source_symbol: Option<Symbol>,
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    pub active_only: bool,
}

impl MarketSelectionQuery {
    pub fn matches(&self, market: &MarketDescriptor) -> bool {
        if self
            .market_id
            .as_deref()
            .is_some_and(|v| market.market_id != *v)
            || self
                .exchange_id
                .as_ref()
                .is_some_and(|v| v != &market.exchange_id)
            || self
                .market_type
                .as_ref()
                .is_some_and(|v| v != &market.market_type)
            || self
                .asset_type
                .is_some_and(|v| market.asset_type.as_ref() != Some(&v))
            || self
                .source_symbol
                .as_deref()
                .is_some_and(|v| !v.eq_ignore_ascii_case(market.source_symbol.as_str()))
            || self
                .source_id
                .as_deref()
                .is_some_and(|v| market.source_id.as_deref() != Some(v))
            || self
                .underlying_instrument_id
                .as_deref()
                .is_some_and(|v| market.underlying_instrument_id.as_deref() != Some(v))
        {
            return false;
        }
        !self.active_only || market.is_active()
    }
}
