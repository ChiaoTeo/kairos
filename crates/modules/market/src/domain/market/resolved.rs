use std::collections::{BTreeMap, BTreeSet};

use kairos_primitives::decimal::Price;
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{
    AssetClass, ExchangeId, InstrumentId, InstrumentKind, MarketId, ReferenceStatus,
};
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

use super::{
    AttachedMarketDataRoute, ProviderRouteBinding, ResolvedMarketDataRoute, ResolvedMarketError,
};
use crate::domain::observation::ObservationScope;
use crate::domain::source::MarketFeedId;
use crate::domain::subscription::ObservationSelector;

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
    #[serde(skip)]
    pub(crate) selected_observations: BTreeSet<ObservationSelector>,
    pub status: ReferenceStatus,
}

impl ResolvedMarket {
    pub(crate) fn from_reference(
        market_id: MarketId,
        instrument_id: InstrumentId,
        instrument_kind: InstrumentKind,
        exchange_id: ExchangeId,
        route: ProviderRouteBinding,
    ) -> Result<Self, ResolvedMarketError> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err(ResolvedMarketError::UnknownInstrumentKind);
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
            selected_observations: BTreeSet::new(),
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
    ) -> Result<Self, ResolvedMarketError> {
        let provider = Provider::new(provider.into()).map_err(|source| {
            ResolvedMarketError::InvalidSemantic {
                field: "provider",
                source,
            }
        })?;
        Self::new_with_routes(
            market_id,
            instrument_id,
            instrument_kind,
            exchange_id,
            BTreeSet::from([provider]),
            BTreeMap::new(),
        )
    }

    #[cfg(test)]
    pub(crate) fn new_with_binding(
        market_id: impl Into<String>,
        instrument_id: impl Into<String>,
        instrument_kind: InstrumentKind,
        exchange_id: impl Into<String>,
        route: ProviderRouteBinding,
    ) -> Result<Self, ResolvedMarketError> {
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
    ) -> Result<Self, ResolvedMarketError> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err(ResolvedMarketError::UnknownInstrumentKind);
        }
        let market_id = MarketId::new(market_id.into()).map_err(|source| {
            ResolvedMarketError::InvalidSemantic {
                field: "market_id",
                source,
            }
        })?;
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
            instrument_id: InstrumentId::new(instrument_id.into()).map_err(|source| {
                ResolvedMarketError::InvalidSemantic {
                    field: "instrument_id",
                    source,
                }
            })?,
            instrument_kind,
            exchange_id: Some(ExchangeId::new(exchange_id).map_err(|source| {
                ResolvedMarketError::InvalidSemantic {
                    field: "exchange_id",
                    source,
                }
            })?),
            asset_type: None,
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            data_routes,
            runtime_routes,
            selected_provider: None,
            selected_observations: BTreeSet::new(),
            status: ReferenceStatus::Active,
        };
        value.validate()?;
        Ok(value)
    }

    /// Resolve a provider route whose observations describe an instrument-wide
    /// or consolidated feed, rather than one canonical exchange market.
    #[cfg(test)]
    pub(crate) fn consolidated(
        instrument_id: impl Into<String>,
        network_id: Option<String>,
        instrument_kind: InstrumentKind,
        route: ProviderRouteBinding,
    ) -> Result<Self, ResolvedMarketError> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err(ResolvedMarketError::UnknownInstrumentKind);
        }
        let instrument_id = InstrumentId::new(instrument_id.into()).map_err(|source| {
            ResolvedMarketError::InvalidSemantic {
                field: "instrument_id",
                source,
            }
        })?;
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
            selected_observations: BTreeSet::new(),
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
    ) -> Result<Self, ResolvedMarketError> {
        if instrument_kind == InstrumentKind::Unknown {
            return Err(ResolvedMarketError::UnknownInstrumentKind);
        }
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
            selected_observations: BTreeSet::new(),
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

    pub(crate) fn merge_data_routes(&mut self, other: &Self) -> Result<(), ResolvedMarketError> {
        if self.scope != other.scope || self.instrument_id != other.instrument_id {
            return Err(ResolvedMarketError::DifferentMarketIdentity {
                current: self.member_id(),
                incoming: other.member_id(),
            });
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

    pub(crate) fn select_observations(
        &mut self,
        selectors: &[ObservationSelector],
    ) -> Result<(), ResolvedMarketError> {
        let route = self
            .runtime_route()
            .ok_or(ResolvedMarketError::MissingRuntimeBinding)?;
        let requested: BTreeSet<ObservationSelector> =
            if selectors.is_empty() || selectors.iter().any(|value| value.kind.is_none()) {
                let capabilities = if route.observation_capabilities.is_empty() {
                    BTreeSet::from([crate::ObservationKind::Quote])
                } else {
                    route.observation_capabilities.clone()
                };
                capabilities
                    .into_iter()
                    .map(|kind| ObservationSelector {
                        kind: Some(kind),
                        qualifier: None,
                    })
                    .collect()
            } else {
                selectors
                    .iter()
                    .map(|selector| {
                        let kind = selector.kind.expect("wildcards were expanded above");
                        let supported = route.observation_capabilities.is_empty()
                            || route.observation_capabilities.contains(&kind)
                            || matches!(
                                kind,
                                crate::ObservationKind::Rate
                                    if route
                                        .observation_capabilities
                                        .contains(&crate::ObservationKind::FundingRate)
                            )
                            || matches!(
                                kind,
                                crate::ObservationKind::FundingRate
                                    if route
                                        .observation_capabilities
                                        .contains(&crate::ObservationKind::Rate)
                            );
                        supported.then(|| selector.clone()).ok_or(
                            ResolvedMarketError::UnsupportedObservation {
                                observation_kind: kind,
                            },
                        )
                    })
                    .collect::<Result<_, _>>()?
            };
        if requested.is_empty() {
            return Err(ResolvedMarketError::NoSubscribableObservations);
        }
        self.selected_observations = requested;
        Ok(())
    }

    pub(crate) fn observation_requirements(&self) -> BTreeSet<ObservationSelector> {
        if !self.selected_observations.is_empty() {
            return self.selected_observations.clone();
        }
        self.runtime_route()
            .into_iter()
            .flat_map(|route| route.observation_capabilities.iter().copied())
            .map(|kind| ObservationSelector {
                kind: Some(kind),
                qualifier: None,
            })
            .collect()
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

    pub fn with_asset_type(
        mut self,
        asset_type: impl Into<String>,
    ) -> Result<Self, ResolvedMarketError> {
        self.asset_type = Some(asset_type.into().parse::<AssetClass>().map_err(|source| {
            ResolvedMarketError::InvalidSemantic {
                field: "asset_type",
                source,
            }
        })?);
        self.validate()?;
        Ok(self)
    }

    pub fn validate(&self) -> Result<(), ResolvedMarketError> {
        if self.instrument_kind == InstrumentKind::Unknown {
            return Err(ResolvedMarketError::UnknownInstrumentKind);
        }
        if self.status == ReferenceStatus::Unknown {
            return Err(ResolvedMarketError::UnknownStatus);
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

#[cfg(test)]
mod tests {
    use kairos_primitives::DomainTypeError;
    use kairos_primitives::reference::InstrumentKind;

    use super::{ResolvedMarket, ResolvedMarketError};

    #[test]
    fn resolution_errors_preserve_category_and_field() {
        let unknown_kind = ResolvedMarket::new(
            "market",
            "instrument",
            InstrumentKind::Unknown,
            "exchange",
            "feed",
        )
        .unwrap_err();
        assert_eq!(unknown_kind, ResolvedMarketError::UnknownInstrumentKind);
        assert_eq!(
            unknown_kind.code(),
            "market.resolution.unknown_instrument_kind"
        );

        let invalid_provider =
            ResolvedMarket::new("market", "instrument", InstrumentKind::Spot, "exchange", "")
                .unwrap_err();
        assert_eq!(
            invalid_provider,
            ResolvedMarketError::InvalidSemantic {
                field: "provider",
                source: DomainTypeError::Empty {
                    type_name: "Provider"
                }
            }
        );
    }
}
