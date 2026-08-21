use std::collections::BTreeMap;

use crate::composition::config::MarketSourceBinding;
use crate::composition::sources::{binding_provider_product, binding_supports_canonical_market};
use crate::{MarketDataRoute, ReconcileMarketUniverse, ResolvedMarket};

pub(crate) fn build_reference_projection(
    sources: &BTreeMap<String, MarketSourceBinding>,
) -> crate::services::reference_projection::ReferenceUniverseProjection {
    use kairos_primitives::reference::InstrumentKind::{Equity, Future, Option, Perpetual, Spot};

    use crate::composition::config::{
        BinanceDerivativeProduct, HyperliquidMarketType, OkxInstrumentType,
    };

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
                MarketSourceBinding::BinanceEquity { .. } => ("broker:binance", vec![Equity]),
                MarketSourceBinding::Massive { product, .. } => (
                    "data_provider:massive",
                    match product {
                        crate::composition::config::MassiveMarketProduct::Equity => vec![Equity],
                        crate::composition::config::MassiveMarketProduct::Options => vec![Option],
                    },
                ),
                MarketSourceBinding::Ibkr { .. } => ("broker:ibkr", vec![Equity]),
            };
            let (provider_id, provider_product) = binding_provider_product(binding);
            Some(
                crate::services::reference_projection::ReferenceSourceProjection {
                    source_id: crate::SourceId::new(source_id)
                        .expect("validated source binding key is a valid source identity"),
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
            && market.instrument_kind == kairos_primitives::reference::InstrumentKind::Spot
        {
            candidates.push((None, "binance", "spot"));
        }
        if candidates.is_empty() {
            continue;
        }
        for (source_id, provider_id, provider_product) in candidates {
            let Some(subscription_symbol) =
                subscription_symbol_for(provider_id, provider_product, market)?
            else {
                continue;
            };
            let route = MarketDataRoute::new(
                format!(
                    "market-route:{}:{}",
                    source_id.unwrap_or(provider_id),
                    market.market_id
                ),
                provider_id,
                provider_product,
                subscription_symbol,
            )?
            .with_observation_capabilities(adapter_observation_capabilities(
                provider_id,
                provider_product,
            ));
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
            if let Some(source_id) = source_id {
                descriptor = descriptor.with_source(source_id)?;
            }
            markets.push(descriptor);
        }
    }

    Ok(ReconcileMarketUniverse {
        generation: snapshot.generation,
        event_sequence: snapshot.event_sequence,
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
        },
        "binance" if provider_product.eq_ignore_ascii_case("options") => {
            vec![Quote, Trade, OrderBook, OptionGreeks]
        },
        "binance" | "okx" | "hyperliquid" => vec![Quote, Trade, OrderBook],
        "massive" => vec![Quote, Trade],
        _ => Vec::new(),
    }
}

fn subscription_symbol_for(
    _provider_id: &str,
    _provider_product: &str,
    market: &kairos_reference_contract::Market,
) -> Result<Option<String>, String> {
    Ok(market.venue_symbol.as_ref().map(ToString::to_string))
}

#[cfg(test)]
pub(super) fn project_market_universe_at_sequence(
    snapshot: &kairos_reference_contract::ReferenceProjectionSnapshot,
    required_sequence: u64,
    sources: &BTreeMap<String, MarketSourceBinding>,
) -> Result<ReconcileMarketUniverse, String> {
    if snapshot.event_sequence < required_sequence.into() {
        return Err(format!(
            "Reference view sequence {} is behind required sequence {}",
            snapshot.event_sequence, required_sequence
        ));
    }
    project_market_universe(snapshot, sources)
}

fn is_active(status: &kairos_primitives::reference::ReferenceStatus) -> bool {
    matches!(
        status,
        kairos_primitives::reference::ReferenceStatus::Active
            | kairos_primitives::reference::ReferenceStatus::Trading
    )
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use crate::composition::config::{MarketSourceBinding, MassiveMarketProduct};

    use super::{project_market_universe, project_market_universe_at_sequence};

    #[test]
    fn maps_reference_view_to_market_owned_universe() {
        let mut snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
            generation: 7.into(),
            event_sequence: 11.into(),
            ..Default::default()
        };
        snapshot
            .instruments
            .push(kairos_reference_contract::Instrument {
                instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                    .unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
                status: "active".into(),
                ..Default::default()
            });
        snapshot.markets.push(kairos_reference_contract::Market {
            market_id: kairos_primitives::reference::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                .unwrap(),
            exchange_id: kairos_primitives::reference::Exchange::new("exchange:binance").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
            venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
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
        assert_eq!(update.markets[0].route.subscription_symbol, "BTCUSDT");
        assert!(
            update.markets[0]
                .route
                .observation_capabilities
                .contains(&crate::ObservationKind::Quote)
        );
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
        snapshot.markets[0].exchange_id =
            kairos_primitives::reference::Exchange::new("exchange:curated").unwrap();
        let update = project_market_universe(&snapshot, &BTreeMap::new()).unwrap();
        assert!(update.markets.is_empty());
    }

    #[test]
    fn equity_market_can_be_projected_for_multiple_provider_sources() {
        let mut snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
            generation: 7.into(),
            event_sequence: 11.into(),
            ..Default::default()
        };
        snapshot
            .instruments
            .push(kairos_reference_contract::Instrument {
                instrument_id: kairos_primitives::reference::InstrumentId::new(
                    "instrument:equity:US:AAPL:common",
                )
                .unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Equity,
                status: "active".into(),
                ..Default::default()
            });
        snapshot.markets.push(kairos_reference_contract::Market {
            market_id: kairos_primitives::reference::MarketId::new("market:nasdaq:equity:AAPL:USD")
                .unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new(
                "instrument:equity:US:AAPL:common",
            )
            .unwrap(),
            exchange_id: kairos_primitives::reference::Exchange::new("exchange:nasdaq").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Equity,
            asset_type: Some(kairos_primitives::reference::AssetClass::Equity),
            venue_symbol: Some(kairos_primitives::reference::Symbol::new("AAPL").unwrap()),
            status: "active".into(),
            ..Default::default()
        });
        let sources = BTreeMap::from([
            (
                "massive-equity".into(),
                MarketSourceBinding::Massive {
                    enabled: true,
                    product: MassiveMarketProduct::Equity,
                    credential_id: "massive".into(),
                    endpoint: None,
                },
            ),
            (
                "binance-equity".into(),
                MarketSourceBinding::BinanceEquity {
                    enabled: true,
                    credential_id: "binance".into(),
                    endpoint: None,
                    snapshot_interval_ms: 1_000,
                },
            ),
        ]);

        let update = project_market_universe(&snapshot, &sources).unwrap();

        assert_eq!(update.markets.len(), 2);
        assert_eq!(
            update
                .markets
                .iter()
                .map(|market| market.member_id())
                .collect::<Vec<_>>(),
            vec![
                "market:nasdaq:equity:AAPL:USD#source:binance-equity",
                "market:nasdaq:equity:AAPL:USD#source:massive-equity",
            ]
        );
    }

    #[test]
    fn explicit_exchange_option_market_projects_to_massive_options_route() {
        let mut snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
            generation: 7.into(),
            event_sequence: 11.into(),
            ..Default::default()
        };
        snapshot
            .instruments
            .push(kairos_reference_contract::Instrument {
                instrument_id: kairos_primitives::reference::InstrumentId::new(
                    "instrument:option:SPY:20270115:500:C",
                )
                .unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Option,
                underlying_instrument_id: Some(
                    kairos_primitives::reference::InstrumentId::new(
                        "instrument:equity:US:SPY:common",
                    )
                    .unwrap(),
                ),
                expiry_unix_nanos: Some(kairos_primitives::time::UnixNanos::new(
                    1_800_144_000_000_000_000,
                )),
                strike: Some(kairos_primitives::decimal::Price::new(500, 0).unwrap()),
                option_right: Some("call".into()),
                status: "active".into(),
                ..Default::default()
            });
        snapshot.markets.push(kairos_reference_contract::Market {
            market_id: kairos_primitives::reference::MarketId::new(
                "market:cboe-bzx-options:option:O:SPY260821C00500000",
            )
            .unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new(
                "instrument:option:SPY:20270115:500:C",
            )
            .unwrap(),
            listing_id: Some(
                kairos_primitives::reference::ListingId::new(
                    "listing:cboe-bzx-options:option:SPY-20270115-500-C",
                )
                .unwrap(),
            ),
            exchange_id: kairos_primitives::reference::Exchange::new("exchange:cboe-bzx-options")
                .unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Option,
            underlying_instrument_id: Some(
                kairos_primitives::reference::InstrumentId::new("instrument:equity:US:SPY:common")
                    .unwrap(),
            ),
            venue_symbol: Some(
                kairos_primitives::reference::Symbol::new("O:SPY260821C00500000").unwrap(),
            ),
            status: "active".into(),
            ..Default::default()
        });
        let sources = BTreeMap::from([(
            "massive-options".into(),
            MarketSourceBinding::Massive {
                enabled: true,
                product: MassiveMarketProduct::Options,
                credential_id: "massive".into(),
                endpoint: None,
            },
        )]);

        let update = project_market_universe(&snapshot, &sources).unwrap();

        assert_eq!(update.markets.len(), 1);
        let market = &update.markets[0];
        assert_eq!(
            market.member_id(),
            "market:cboe-bzx-options:option:O:SPY260821C00500000#source:massive-options"
        );
        assert_eq!(
            market.instrument_kind,
            kairos_primitives::reference::InstrumentKind::Option
        );
        assert_eq!(
            market.underlying_instrument_id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(market.option_right.as_deref(), Some("call"));
        assert_eq!(market.route.provider_id, "massive");
        assert_eq!(market.route.provider_product, "options");
        assert_eq!(market.route.subscription_symbol, "O:SPY260821C00500000");
        assert!(
            market
                .route
                .observation_capabilities
                .contains(&crate::ObservationKind::Quote)
        );
        assert!(
            market
                .route
                .observation_capabilities
                .contains(&crate::ObservationKind::Trade)
        );
    }

    #[test]
    fn rejects_a_view_behind_the_required_event_sequence() {
        let snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
            event_sequence: 4.into(),
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
                instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                    .unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
                status: "active".into(),
                ..Default::default()
            });
        snapshot.markets.push(kairos_reference_contract::Market {
            market_id: kairos_primitives::reference::MarketId::new("market:btc").unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                .unwrap(),
            exchange_id: kairos_primitives::reference::Exchange::new("exchange:binance").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
            venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
            status: "active".into(),
            ..Default::default()
        });
        snapshot
    }
}
