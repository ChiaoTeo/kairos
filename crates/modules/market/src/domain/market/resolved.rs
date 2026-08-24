use std::collections::{BTreeMap, BTreeSet};

use kairos_primitives::decimal::Price;
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{
    AssetClass, ExchangeId, InstrumentId, InstrumentKind, MarketId, ReferenceStatus,
};
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

use super::{AttachedMarketDataRoute, ProviderRouteBinding, ResolvedMarketDataRoute};
use crate::domain::observation::ObservationScope;
use crate::domain::source::MarketFeedId;

/// Canonical Market facts and the providers able to satisfy Market observations.
/// Provider-native product, symbol and runtime feed identity remain private.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ResolvedMarket {
    pub scope: ObservationScope,
    pub instrument_id: InstrumentId,
    pub instrument_kind: InstrumentKind,
    pub exchange_id: Option<ExchangeId>,
    #[serde(default)]
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    #[serde(default)]
    pub expiry_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub strike: Option<Price>,
    #[serde(default)]
    pub option_right: Option<String>,
    /// Business provider routes for this canonical Market.
    #[serde(default)]
    pub data_routes: BTreeSet<ResolvedMarketDataRoute>,
    #[serde(skip)]
    pub(crate) runtime_routes: BTreeMap<Provider, ProviderRouteBinding>,
    #[serde(skip)]
    pub(crate) selected_provider: Option<Provider>,
    pub status: ReferenceStatus,
}

impl ResolvedMarket {
    pub(crate) fn from_reference(
        market_id: MarketId,
        instrument_id: InstrumentId,
        instrument_kind: InstrumentKind,
        exchange_id: ExchangeId,
        route: ProviderRouteBinding,
    ) -> Result<Self, String> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        let data_route = ResolvedMarketDataRoute::new(
            market_id.clone(),
            route.provider.clone(),
            route.observation_capabilities.iter().copied(),
        );
        let runtime_routes = BTreeMap::from([(route.provider.clone(), route.clone())]);
        let value = Self {
            scope: ObservationScope::from(market_id),
            instrument_id,
            instrument_kind,
            exchange_id: Some(exchange_id),
            asset_type: None,
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            data_routes: BTreeSet::from([data_route]),
            runtime_routes,
            selected_provider: None,
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
        provider: impl Into<String>,
    ) -> Result<Self, String> {
        let provider = Provider::new(provider.into()).map_err(|error| error.to_string())?;
        Self::new_with_routes(
            market_id,
            instrument_id,
            instrument_kind,
            exchange_id,
            BTreeSet::from([provider]),
            BTreeMap::new(),
        )
    }

    pub(crate) fn new_with_binding(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        instrument_kind: InstrumentKind,
        exchange_id: impl Into<String>,
        route: ProviderRouteBinding,
    ) -> Result<Self, String> {
        let providers = BTreeSet::from([route.provider.clone()]);
        let runtime_routes = BTreeMap::from([(route.provider.clone(), route)]);
        Self::new_with_routes(
            market_id,
            instrument_id,
            instrument_kind,
            exchange_id,
            providers,
            runtime_routes,
        )
    }

    fn new_with_routes(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        instrument_kind: InstrumentKind,
        exchange_id: impl Into<String>,
        providers: BTreeSet<Provider>,
        runtime_routes: BTreeMap<Provider, ProviderRouteBinding>,
    ) -> Result<Self, String> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        let market_id = MarketId::new(market_id.into()).map_err(|error| error.to_string())?;
        let data_routes = providers
            .into_iter()
            .map(|provider| {
                let observations = runtime_routes
                    .get(&provider)
                    .map(|binding| binding.observation_capabilities.iter().copied())
                    .into_iter()
                    .flatten();
                ResolvedMarketDataRoute::new(market_id.clone(), provider, observations)
            })
            .collect();
        let value = Self {
            scope: ObservationScope::market(market_id.to_string())?,
            instrument_id: InstrumentId::new(instrument_id.into())
                .map_err(|error| error.to_string())?,
            instrument_kind,
            exchange_id: Some(ExchangeId::new(exchange_id).map_err(|error| error.to_string())?),
            asset_type: None,
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            data_routes,
            runtime_routes,
            selected_provider: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    /// Resolve a provider route whose observations describe an instrument-wide
    /// or consolidated feed, rather than one canonical exchange market.
    pub(crate) fn consolidated(
        instrument_id: impl Into<String>,
        network_id: Option<String>,
        instrument_kind: InstrumentKind,
        route: ProviderRouteBinding,
    ) -> Result<Self, String> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err("market instrument kind must be known".into());
        }
        let instrument_id =
            InstrumentId::new(instrument_id.into()).map_err(|error| error.to_string())?;
        let runtime_routes = BTreeMap::from([(route.provider.clone(), route.clone())]);
        let value = Self {
            scope: ObservationScope::consolidated(instrument_id.to_string(), network_id)?,
            instrument_id,
            instrument_kind,
            exchange_id: None,
            asset_type: None,
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            data_routes: BTreeSet::new(),
            runtime_routes,
            selected_provider: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn consolidated_reference(
        instrument_id: InstrumentId,
        network_id: Option<String>,
        instrument_kind: InstrumentKind,
        route: ProviderRouteBinding,
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
        let runtime_routes = BTreeMap::from([(route.provider.clone(), route.clone())]);
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
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            data_routes: BTreeSet::new(),
            runtime_routes,
            selected_provider: None,
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn market_id(&self) -> Option<&MarketId> {
        self.scope.market_id()
    }

    pub fn data_route(&self) -> Option<ResolvedMarketDataRoute> {
        self.data_routes.iter().next().cloned()
    }

    pub(crate) fn merge_data_routes(&mut self, other: &Self) -> Result<(), String> {
        if self.scope != other.scope || self.instrument_id != other.instrument_id {
            return Err("cannot merge provider routes for different Markets".into());
        }
        self.data_routes.extend(other.data_routes.iter().cloned());
        self.runtime_routes.extend(other.runtime_routes.clone());
        Ok(())
    }

    pub(crate) fn attach_route(
        &self,
        feed_id: &crate::domain::source::MarketFeedId,
        provider: &kairos_primitives::market::Provider,
    ) -> Option<AttachedMarketDataRoute> {
        let runtime = self.runtime_routes.get(provider)?;
        let route = self
            .data_routes
            .iter()
            .find(|route| &route.provider == provider)?
            .clone();
        Some(AttachedMarketDataRoute {
            route,
            feed_id: MarketFeedId::new(feed_id.as_str()).ok()?,
            provider_segment: runtime.provider_segment.clone(),
            subscription_symbol: runtime.subscription_symbol.clone(),
        })
    }

    pub(crate) fn runtime_route(&self) -> Option<&ProviderRouteBinding> {
        self.selected_provider
            .as_ref()
            .and_then(|provider| self.runtime_routes.get(provider))
            .or_else(|| {
                (self.runtime_routes.len() == 1)
                    .then(|| self.runtime_routes.values().next())
                    .flatten()
            })
    }

    pub(crate) fn retain_provider(&mut self, provider: &Provider) -> bool {
        if !self.runtime_routes.contains_key(provider) {
            return false;
        }
        self.runtime_routes
            .retain(|candidate, _| candidate == provider);
        self.data_routes.retain(|route| &route.provider == provider);
        self.selected_provider = Some(provider.clone());
        true
    }

    pub fn member_id(&self) -> String {
        self.scope.key()
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
