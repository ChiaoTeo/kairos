use std::collections::BTreeMap;

use kairos_primitives::InstrumentKind;

use crate::{MarketDataRoute, ObservationKind, ReconcileMarketUniverse, ResolvedMarket};

#[derive(Clone)]
pub(crate) struct ReferenceSourceProjection {
    pub(crate) source_id: String,
    pub(crate) provider_id: String,
    pub(crate) provider_product: String,
    pub(crate) exchange_id: String,
    pub(crate) instrument_kinds: Vec<InstrumentKind>,
}

#[derive(Clone, Default)]
pub(crate) struct ReferenceUniverseProjection {
    sources: Vec<ReferenceSourceProjection>,
}

impl ReferenceUniverseProjection {
    pub(crate) fn new(sources: Vec<ReferenceSourceProjection>) -> Self {
        Self { sources }
    }

    pub(crate) fn project(
        &self,
        snapshot: &kairos_reference_contract::ReferenceProjectionSnapshot,
        required_sequence: u64,
    ) -> Result<ReconcileMarketUniverse, String> {
        if snapshot.event_sequence < required_sequence {
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
            .filter(|market| matches!(market.status.as_str(), "active" | "trading"))
        {
            let instrument = instruments
                .get(market.instrument_id.as_str())
                .ok_or_else(|| {
                    format!(
                        "Reference market {} has no instrument {}",
                        market.market_id, market.instrument_id
                    )
                })?;
            let Some(provider_symbol) = market.venue_symbol.as_deref() else {
                continue;
            };
            let mut candidates = self
                .sources
                .iter()
                .filter(|source| {
                    source.exchange_id.eq_ignore_ascii_case(&market.exchange_id)
                        && source.instrument_kinds.contains(&market.instrument_kind)
                })
                .map(|source| {
                    (
                        Some(source.source_id.as_str()),
                        source.provider_id.as_str(),
                        source.provider_product.as_str(),
                    )
                })
                .collect::<Vec<_>>();
            if candidates.is_empty()
                && market.exchange_id.eq_ignore_ascii_case("exchange:binance")
                && market.instrument_kind == InstrumentKind::Spot
            {
                candidates.push((None, "binance", "spot"));
            }
            let [(source_id, provider_id, provider_product)] = candidates.as_slice() else {
                if candidates.is_empty() {
                    continue;
                }
                return Err(format!(
                    "canonical market {} has ambiguous Market source bindings: {}",
                    market.market_id,
                    candidates
                        .iter()
                        .filter_map(|(source_id, _, _)| *source_id)
                        .collect::<Vec<_>>()
                        .join(",")
                ));
            };
            let route = MarketDataRoute::new(
                format!(
                    "market-route:{}:{}",
                    source_id.unwrap_or(provider_id),
                    market.market_id
                ),
                *provider_id,
                *provider_product,
                provider_symbol,
            )?
            .with_observation_capabilities(observation_capabilities(provider_id, provider_product));
            let mut descriptor = ResolvedMarket::new(
                market.market_id.clone(),
                market.instrument_id.clone(),
                instrument.instrument_type,
                market.exchange_id.clone(),
                route,
            )?;
            descriptor.asset_type = market.asset_type;
            descriptor.underlying_instrument_id = market
                .underlying_instrument_id
                .clone()
                .map(kairos_primitives::InstrumentId::new)
                .transpose()
                .map_err(|error| error.to_string())?;
            if let Some(source_id) = source_id {
                descriptor = descriptor.with_source(*source_id)?;
            }
            markets.push(descriptor);
        }
        Ok(ReconcileMarketUniverse {
            generation: snapshot.generation.into(),
            event_sequence: snapshot.event_sequence.into(),
            markets,
        })
    }
}

pub(crate) fn observation_capabilities(
    provider_id: &str,
    provider_product: &str,
) -> Vec<ObservationKind> {
    use ObservationKind::{Bar, OptionGreeks, OrderBook, Quote, Trade};
    match provider_id.to_ascii_lowercase().as_str() {
        "binance" if provider_product.eq_ignore_ascii_case("spot") => {
            vec![Quote, Trade, Bar, OrderBook]
        }
        "binance" if provider_product.eq_ignore_ascii_case("options") => {
            vec![Quote, Trade, OrderBook, OptionGreeks]
        }
        "binance" | "okx" | "hyperliquid" => vec![Quote, Trade, OrderBook],
        "massive" => vec![Quote, Trade],
        _ => Vec::new(),
    }
}
