use std::collections::BTreeMap;

use kairos_primitives::market::Provider;
use kairos_primitives::reference::{ExchangeId, InstrumentKind, ReferenceStatus};

use super::ReconcileMarketUniverse;
use crate::{ObservationKind, ProviderRouteBinding, ResolvedMarket};

/// One configured provider adapter's ability to serve canonical Reference markets.
#[derive(Clone)]
pub(crate) struct MarketProviderCapability {
    pub(crate) provider: Provider,
    pub(crate) provider_segment: crate::domain::market::ProviderSegmentCode,
    pub(crate) venue_id: Option<ExchangeId>,
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
        snapshot: &kairos_reference_contract::MarketReferenceSnapshot,
        required_sequence: u64,
    ) -> Result<ReconcileMarketUniverse, String> {
        if snapshot.event_sequence < required_sequence.into() {
            return Err(format!(
                "Reference view sequence {} is behind required sequence {}",
                snapshot.event_sequence, required_sequence
            ));
        }
        let instruments = snapshot
            .instruments
            .iter()
            .map(|instrument| (instrument.instrument_id.as_str(), instrument))
            .collect::<BTreeMap<_, _>>();
        let mut markets = Vec::new();
        for market in snapshot
            .markets
            .iter()
            .filter(|market| is_active(&market.status))
        {
            let instrument = instruments
                .get(market.instrument_id.as_str())
                .ok_or_else(|| {
                    format!(
                        "Reference market {} has no instrument {}",
                        market.market_id, market.instrument_id
                    )
                })?;
            let candidates = self
                .providers
                .iter()
                .filter(|source| source.supports(&market.exchange_id, market.instrument_kind))
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
                    market.exchange_id.clone(),
                    route,
                )?;
                descriptor.asset_type = market.asset_type;
                descriptor.underlying_instrument_id = market.underlying_instrument_id.clone();
                descriptor.expiry_unix_nanos = instrument.expiry_unix_nanos;
                descriptor.strike = instrument.strike;
                descriptor.option_right = instrument.option_right.clone();
                markets.push(descriptor);
            }
        }

        Ok(ReconcileMarketUniverse {
            generation: snapshot.generation,
            event_sequence: snapshot.event_sequence,
            markets: merge_markets(markets)?,
        })
    }
}

impl MarketProviderCapability {
    fn supports(
        &self,
        exchange_id: &kairos_primitives::reference::ExchangeId,
        instrument_kind: InstrumentKind,
    ) -> bool {
        self.venue_id
            .as_ref()
            .is_none_or(|venue| venue == exchange_id)
            && self.instrument_kinds.contains(&instrument_kind)
    }
}

fn merge_markets(markets: Vec<ResolvedMarket>) -> Result<Vec<ResolvedMarket>, String> {
    let mut merged = BTreeMap::<String, ResolvedMarket>::new();
    for market in markets {
        let key = market.member_id();
        if let Some(existing) = merged.get_mut(&key) {
            existing.merge_data_routes(&market)?;
        } else {
            merged.insert(key, market);
        }
    }
    Ok(merged.into_values().collect())
}

fn is_active(status: &ReferenceStatus) -> bool {
    matches!(status, ReferenceStatus::Active | ReferenceStatus::Trading)
}
