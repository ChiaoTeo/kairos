use std::collections::BTreeMap;

use crate::composition::config::MarketSourceBinding;
use crate::composition::sources::{binding_provider_product, binding_supports_canonical_market};
use crate::{MarketDataRoute, ReconcileMarketUniverse, ResolvedMarket};

pub(crate) fn build_reference_projection(
    sources: &BTreeMap<String, MarketSourceBinding>,
) -> crate::services::reference_projection::ReferenceUniverseProjection {
    use crate::composition::config::{
        BinanceDerivativeProduct, HyperliquidMarketType, OkxInstrumentType,
    };
    use kairos_primitives::InstrumentKind::{Future, Option, Perpetual, Spot};

    let sources = sources
        .iter()
        .filter(|(_, binding)| binding.enabled())
        .filter_map(|(source_id, binding)| {
            let (exchange_id, instrument_kinds) = match binding {
                MarketSourceBinding::BinanceSpot { .. } => ("exchange:binance", vec![Spot]),
                MarketSourceBinding::BinanceDerivatives { product, .. } => (
                    "exchange:binance",
                    match product {
                        BinanceDerivativeProduct::Options => vec![Option],
                        BinanceDerivativeProduct::UsdMFutures
                        | BinanceDerivativeProduct::CoinMFutures => vec![Future, Perpetual],
                    },
                ),
                MarketSourceBinding::Okx {
                    instrument_type, ..
                } => (
                    "exchange:okx",
                    vec![match instrument_type {
                        OkxInstrumentType::Spot => Spot,
                        OkxInstrumentType::Swap => Perpetual,
                        OkxInstrumentType::Futures => Future,
                        OkxInstrumentType::Options => Option,
                    }],
                ),
                MarketSourceBinding::Hyperliquid { market_type, .. } => (
                    "exchange:hyperliquid",
                    vec![match market_type {
                        HyperliquidMarketType::Spot => Spot,
                        HyperliquidMarketType::Perpetual => Perpetual,
                    }],
                ),
                MarketSourceBinding::BinanceEquity { .. }
                | MarketSourceBinding::Massive { .. }
                | MarketSourceBinding::Ibkr { .. } => return None,
            };
            let (provider_id, provider_product) = binding_provider_product(binding);
            Some(
                crate::services::reference_projection::ReferenceSourceProjection {
                    source_id: source_id.clone(),
                    provider_id: provider_id.into(),
                    provider_product: provider_product.into(),
                    exchange_id: exchange_id.into(),
                    instrument_kinds,
                },
            )
        })
        .collect();
    crate::services::reference_projection::ReferenceUniverseProjection::new(sources)
}

pub(crate) fn project_market_universe(
    snapshot: &kairos_reference_contract::ReferenceProjectionSnapshot,
    sources: &BTreeMap<String, MarketSourceBinding>,
) -> Result<ReconcileMarketUniverse, String> {
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
        let Some(provider_symbol) = market.venue_symbol.as_deref() else {
            continue;
        };
        let mut candidates = sources
            .iter()
            .filter(|(_, binding)| {
                binding.enabled()
                    && binding_supports_canonical_market(
                        binding,
                        &market.exchange_id,
                        market.instrument_kind,
                    )
            })
            .map(|(source_id, binding)| {
                let (provider_id, provider_product) = binding_provider_product(binding);
                (Some(source_id.as_str()), provider_id, provider_product)
            })
            .collect::<Vec<_>>();
        if candidates.is_empty()
            && market.exchange_id.eq_ignore_ascii_case("exchange:binance")
            && market.instrument_kind == kairos_primitives::InstrumentKind::Spot
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
        .with_observation_capabilities(adapter_observation_capabilities(
            provider_id,
            provider_product,
        ));
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

pub(crate) fn adapter_observation_capabilities(
    provider_id: &str,
    provider_product: &str,
) -> Vec<crate::ObservationKind> {
    use crate::ObservationKind::{Bar, OptionGreeks, OrderBook, Quote, Trade};
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

#[cfg(test)]
pub(super) fn project_market_universe_at_sequence(
    snapshot: &kairos_reference_contract::ReferenceProjectionSnapshot,
    required_sequence: u64,
    sources: &BTreeMap<String, MarketSourceBinding>,
) -> Result<ReconcileMarketUniverse, String> {
    if snapshot.event_sequence < required_sequence {
        return Err(format!(
            "Reference view sequence {} is behind required sequence {}",
            snapshot.event_sequence, required_sequence
        ));
    }
    project_market_universe(snapshot, sources)
}

fn is_active(status: &str) -> bool {
    matches!(status, "active" | "trading")
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::{project_market_universe, project_market_universe_at_sequence};

    #[test]
    fn maps_reference_view_to_market_owned_universe() {
        let mut snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
            generation: 7,
            event_sequence: 11,
            ..Default::default()
        };
        snapshot
            .instruments
            .push(kairos_reference_contract::Instrument {
                instrument_id: "instrument:btc".into(),
                instrument_type: kairos_primitives::InstrumentKind::Spot,
                status: "active".into(),
                ..Default::default()
            });
        snapshot.markets.push(kairos_reference_contract::Market {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            exchange_id: "exchange:binance".into(),
            instrument_kind: kairos_primitives::InstrumentKind::Spot,
            venue_symbol: Some("BTCUSDT".into()),
            status: "active".into(),
            ..Default::default()
        });
        let update = project_market_universe(&snapshot, &BTreeMap::new()).unwrap();
        assert_eq!(update.generation.get(), 7);
        assert_eq!(update.event_sequence.get(), 11);
        assert_eq!(update.markets.len(), 1);
        assert_eq!(update.markets[0].market_id().unwrap(), "market:btc");
        assert_eq!(
            update.markets[0].route.route_id,
            "market-route:binance:market:btc"
        );
        assert_eq!(update.markets[0].route.provider_id, "binance");
        assert_eq!(update.markets[0].route.provider_product, "spot");
        assert_eq!(update.markets[0].route.provider_symbol, "BTCUSDT");
        assert!(update.markets[0]
            .route
            .observation_capabilities
            .contains(&crate::ObservationKind::Quote));
    }

    #[test]
    fn excludes_market_without_venue_symbol() {
        let mut snapshot = fixture();
        snapshot.markets[0].venue_symbol = None;
        let update = project_market_universe(&snapshot, &BTreeMap::new()).unwrap();
        assert!(update.markets.is_empty());
    }

    #[test]
    fn excludes_market_without_a_configured_or_builtin_venue_adapter() {
        let mut snapshot = fixture();
        snapshot.markets[0].exchange_id = "exchange:curated".into();
        let update = project_market_universe(&snapshot, &BTreeMap::new()).unwrap();
        assert!(update.markets.is_empty());
    }

    #[test]
    fn rejects_a_view_behind_the_required_event_sequence() {
        let snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
            event_sequence: 4,
            ..Default::default()
        };
        let error =
            project_market_universe_at_sequence(&snapshot, 5, &BTreeMap::new()).unwrap_err();
        assert!(error.contains("behind required sequence 5"));
    }

    fn fixture() -> kairos_reference_contract::ReferenceProjectionSnapshot {
        let mut snapshot = kairos_reference_contract::ReferenceProjectionSnapshot::default();
        snapshot
            .instruments
            .push(kairos_reference_contract::Instrument {
                instrument_id: "instrument:btc".into(),
                instrument_type: kairos_primitives::InstrumentKind::Spot,
                status: "active".into(),
                ..Default::default()
            });
        snapshot.markets.push(kairos_reference_contract::Market {
            market_id: "market:btc".into(),
            instrument_id: "instrument:btc".into(),
            exchange_id: "exchange:binance".into(),
            instrument_kind: kairos_primitives::InstrumentKind::Spot,
            venue_symbol: Some("BTCUSDT".into()),
            status: "active".into(),
            ..Default::default()
        });
        snapshot
    }
}
