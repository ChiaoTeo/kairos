use std::collections::BTreeMap;

use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentKind, ReferenceStatus, VenueId};

use super::ReconcileMarketUniverse;
use crate::{ObservationKind, ProviderRouteBinding, ResolvedMarket};

/// One configured provider adapter's ability to serve canonical Reference markets.
#[derive(Clone)]
pub(crate) struct MarketProviderCapability {
    pub(crate) provider: Provider,
    pub(crate) provider_segment: crate::domain::market::ProviderSegmentCode,
    pub(crate) execution_venue_id: Option<VenueId>,
    pub(crate) instrument_kinds: Vec<InstrumentKind>,
    pub(crate) observation_kinds: Vec<ObservationKind>,
}

/// Resolves a Reference catalog view into the universe Market can actually serve.
#[derive(Clone, Default)]
pub(crate) struct MarketUniverseResolver {
    providers: Vec<MarketProviderCapability>,
}

impl MarketUniverseResolver {
    pub(crate) fn new(providers: Vec<MarketProviderCapability>) -> Self {
        Self { providers }
    }

    pub(crate) fn resolve(
        &self,
        catalog: &kairos_reference_contract::MarketSearchResponse,
    ) -> Result<ReconcileMarketUniverse, String> {
        let instruments = &catalog.instruments;
        let mut markets = Vec::new();
        for market in catalog
            .markets
            .iter()
            .filter(|market| is_active(&market.status))
        {
            let instrument = instruments.get(&market.instrument_id).ok_or_else(|| {
                format!(
                    "Reference market {} has no instrument {}",
                    market.market_id, market.instrument_id
                )
            })?;
            let candidates = self
                .providers
                .iter()
                .filter(|source| {
                    source.supports(&market.execution_venue_id, instrument.instrument_type)
                })
                .map(|source| {
                    (
                        source.provider.as_str(),
                        source.provider_segment.as_str(),
                        source.observation_kinds.as_slice(),
                    )
                })
                .collect::<Vec<_>>();
            for (provider_id, provider_segment, observation_kinds) in candidates {
                let Some(subscription_symbol) = market.venue_symbol.as_ref() else {
                    continue;
                };
                let route = ProviderRouteBinding::new(
                    provider_id,
                    provider_segment,
                    subscription_symbol.to_string(),
                )?
                .with_observation_capabilities(observation_kinds.iter().copied());
                let mut descriptor = ResolvedMarket::from_reference(
                    market.market_id.clone(),
                    market.instrument_id.clone(),
                    instrument.instrument_type,
                    market.execution_venue_id.clone(),
                    route,
                )
                .map_err(|error| error.to_string())?;
                descriptor.asset_type = None;
                descriptor.underlying_instrument_id = instrument.underlying_instrument_id.clone();
                descriptor.expiry_unix_nanos = instrument.expiry_unix_nanos;
                descriptor.strike = instrument.strike;
                descriptor.option_right = instrument.option_right.clone();
                markets.push(descriptor);
            }
        }

        Ok(ReconcileMarketUniverse {
            generation: catalog.evidence.watermark.generation,
            event_sequence: catalog.evidence.watermark.event_sequence,
            markets: merge_markets(markets)?,
        })
    }

    /// Resolve one bounded, transaction-consistent Reference query result.
    /// The returned descriptors belong to the caller's current demand; this
    /// method does not create or update a Market-side Reference catalog.
    pub(crate) fn resolve_catalog_page(
        &self,
        page: kairos_reference_contract::MarketSearchResponse,
    ) -> Result<Vec<ResolvedMarket>, String> {
        self.resolve(&page).map(|resolved| resolved.markets)
    }
}

impl MarketProviderCapability {
    fn supports(&self, execution_venue_id: &VenueId, instrument_kind: InstrumentKind) -> bool {
        self.execution_venue_id
            .as_ref()
            .is_none_or(|venue| venue == execution_venue_id)
            && self.instrument_kinds.contains(&instrument_kind)
    }
}

fn merge_markets(markets: Vec<ResolvedMarket>) -> Result<Vec<ResolvedMarket>, String> {
    let mut merged = BTreeMap::<String, ResolvedMarket>::new();
    for market in markets {
        let key = market.member_id();
        if let Some(existing) = merged.get_mut(&key) {
            existing
                .merge_data_routes(&market)
                .map_err(|error| error.to_string())?;
        } else {
            merged.insert(key, market);
        }
    }
    Ok(merged.into_values().collect())
}

fn is_active(status: &ReferenceStatus) -> bool {
    matches!(status, ReferenceStatus::Active | ReferenceStatus::Trading)
}
