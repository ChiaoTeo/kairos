use std::collections::BTreeMap;

use crate::ReconcileMarketUniverse;
use crate::composition::config::MarketProviderBinding;
use crate::composition::sources::{binding_observation_capabilities, binding_provider_segment};

pub(crate) fn build_market_universe_resolver(
    sources: &BTreeMap<String, MarketProviderBinding>,
) -> crate::application::MarketUniverseResolver {
    use kairos_primitives::reference::InstrumentKind::{
        Equity, Future, Index, Option, Perpetual, Spot,
    };

    use crate::composition::config::{
        BinanceDerivativeProduct, HyperliquidMarketType, OkxInstrumentType,
    };

    let sources = sources
        .iter()
        .filter(|(_, binding)| binding.enabled())
        .map(|(_, binding)| {
            let (execution_venue_id, instrument_kinds) = match binding {
                MarketProviderBinding::BinanceSpot { .. } => (Some("venue:binance"), vec![Spot]),
                MarketProviderBinding::BinanceDerivatives { product, .. } => (
                    Some("venue:binance"),
                    match product {
                        BinanceDerivativeProduct::Options => vec![Option],
                        BinanceDerivativeProduct::UsdMFutures
                        | BinanceDerivativeProduct::CoinMFutures => vec![Future, Perpetual],
                    },
                ),
                MarketProviderBinding::Okx {
                    instrument_type, ..
                } => (
                    Some("venue:okx"),
                    vec![match instrument_type {
                        OkxInstrumentType::Spot => Spot,
                        OkxInstrumentType::Swap => Perpetual,
                        OkxInstrumentType::Futures => Future,
                        OkxInstrumentType::Options => Option,
                    }],
                ),
                MarketProviderBinding::Hyperliquid { market_type, .. } => (
                    Some("venue:hyperliquid"),
                    vec![match market_type {
                        HyperliquidMarketType::Spot => Spot,
                        HyperliquidMarketType::Perpetual => Perpetual,
                    }],
                ),
                MarketProviderBinding::BinanceEquity { .. } => (None, vec![Equity]),
                MarketProviderBinding::Massive { product, .. } => (
                    None,
                    match product {
                        crate::composition::config::MassiveMarketProduct::Equity => vec![Equity],
                        crate::composition::config::MassiveMarketProduct::Options => vec![Option],
                        crate::composition::config::MassiveMarketProduct::Futures => vec![Future],
                        crate::composition::config::MassiveMarketProduct::Indices => vec![Index],
                        crate::composition::config::MassiveMarketProduct::Forex
                        | crate::composition::config::MassiveMarketProduct::Crypto => vec![Spot],
                    },
                ),
                MarketProviderBinding::Ibkr { .. } => (None, vec![Equity]),
            };
            let (provider_id, provider_segment) = binding_provider_segment(binding);
            crate::application::MarketProviderCapability {
                provider: kairos_primitives::market::Provider::new(provider_id)
                    .expect("code-owned provider identity is valid"),
                provider_segment: crate::domain::market::ProviderSegmentCode::new(provider_segment)
                    .expect("code-owned provider segment is valid"),
                execution_venue_id: execution_venue_id
                    .map(kairos_primitives::reference::VenueId::new)
                    .transpose()
                    .expect("code-owned exchange identity is valid"),
                instrument_kinds,
                observation_kinds: binding_observation_capabilities(binding),
            }
        })
        .collect();
    crate::application::MarketUniverseResolver::new(sources)
}

pub fn resolve_market_universe(
    catalog: &kairos_reference_contract::MarketSearchResponse,
    sources: &BTreeMap<String, MarketProviderBinding>,
) -> Result<ReconcileMarketUniverse, String> {
    build_market_universe_resolver(sources).resolve(catalog)
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::resolve_market_universe;
    use crate::composition::config::{MarketProviderBinding, MassiveMarketProduct};

    #[test]
    fn maps_reference_view_to_market_owned_universe() {
        let mut snapshot = kairos_reference_contract::MarketSearchResponse {
            evidence: evidence(),
            ..Default::default()
        };
        let instrument = kairos_reference_contract::Instrument {
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                .unwrap(),
            instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
            status: "active".into(),
            ..Default::default()
        };
        snapshot
            .instruments
            .insert(instrument.instrument_id.clone(), instrument);
        snapshot.markets.push(venue_market(
            "market:btc",
            "instrument:btc",
            "venue:binance",
            "BTCUSDT",
        ));
        let sources = BTreeMap::from([(
            "binance-spot-rest".into(),
            MarketProviderBinding::BinanceSpot {
                enabled: true,
                connection_id: None,
                transport: crate::composition::config::BinanceSpotTransport::Rest,
                endpoint: None,
                snapshot_interval_ms: 1_000,
            },
        )]);
        let update = resolve_market_universe(&snapshot, &sources).unwrap();
        assert_eq!(update.generation.get(), 7);
        assert_eq!(update.event_sequence.get(), 11);
        assert_eq!(update.markets.len(), 1);
        assert_eq!(update.markets[0].market_id().unwrap(), "market:btc");
        let binding = update.markets[0].runtime_route().unwrap();
        assert_eq!(binding.provider.as_str(), "binance");
        assert_eq!(binding.provider_segment, "spot");
        assert_eq!(binding.subscription_symbol, "BTCUSDT");
        assert!(
            binding
                .observation_capabilities
                .contains(&crate::ObservationKind::Quote)
        );
    }

    #[test]
    fn excludes_market_without_venue_symbol() {
        let mut snapshot = fixture();
        snapshot.markets[0].venue_symbol = None;
        let update = resolve_market_universe(&snapshot, &BTreeMap::new()).unwrap();
        assert!(update.markets.is_empty());
    }

    #[test]
    fn excludes_market_without_a_configured_or_builtin_venue_adapter() {
        let mut snapshot = fixture();
        snapshot.markets[0].execution_venue_id =
            kairos_primitives::reference::VenueId::new("venue:curated").unwrap();
        let update = resolve_market_universe(&snapshot, &BTreeMap::new()).unwrap();
        assert!(update.markets.is_empty());
    }

    #[test]
    fn equity_market_can_be_resolved_for_multiple_provider_sources() {
        let mut snapshot = kairos_reference_contract::MarketSearchResponse {
            evidence: evidence(),
            ..Default::default()
        };
        let instrument = kairos_reference_contract::Instrument {
            instrument_id: kairos_primitives::reference::InstrumentId::new(
                "instrument:equity:US:AAPL:common",
            )
            .unwrap(),
            instrument_type: kairos_primitives::reference::InstrumentKind::Equity,
            status: "active".into(),
            ..Default::default()
        };
        snapshot
            .instruments
            .insert(instrument.instrument_id.clone(), instrument);
        snapshot.markets.push(venue_market(
            "market:nasdaq:equity:AAPL:USD",
            "instrument:equity:US:AAPL:common",
            "venue:xnas",
            "AAPL",
        ));
        let sources = BTreeMap::from([
            (
                "massive-equity".into(),
                MarketProviderBinding::Massive {
                    enabled: true,
                    connection_id: None,
                    product: MassiveMarketProduct::Equity,
                    credential_id: Some("massive".into()),
                    endpoint: None,
                },
            ),
            (
                "binance-equity".into(),
                MarketProviderBinding::BinanceEquity {
                    enabled: true,
                    connection_id: None,
                    credential_id: Some("binance".into()),
                    endpoint: None,
                    snapshot_interval_ms: 1_000,
                },
            ),
        ]);

        let update = resolve_market_universe(&snapshot, &sources).unwrap();

        assert_eq!(update.markets.len(), 1);
        assert_eq!(
            update.markets[0].member_id(),
            "market:nasdaq:equity:AAPL:USD"
        );
        assert_eq!(
            update.markets[0]
                .data_routes
                .iter()
                .map(|route| route.provider.as_str())
                .collect::<Vec<_>>(),
            vec!["binance", "massive"]
        );
    }

    #[test]
    fn explicit_exchange_option_market_projects_to_massive_options_route() {
        let mut snapshot = kairos_reference_contract::MarketSearchResponse {
            evidence: evidence(),
            ..Default::default()
        };
        let instrument = kairos_reference_contract::Instrument {
            instrument_id: kairos_primitives::reference::InstrumentId::new(
                "instrument:option:SPY:20270115:500:C",
            )
            .unwrap(),
            instrument_type: kairos_primitives::reference::InstrumentKind::Option,
            underlying_instrument_id: Some(
                kairos_primitives::reference::InstrumentId::new("instrument:equity:US:SPY:common")
                    .unwrap(),
            ),
            expiry_unix_nanos: Some(kairos_primitives::time::UnixNanos::new(
                1_800_144_000_000_000_000,
            )),
            strike: Some(kairos_primitives::decimal::Price::new(500, 0).unwrap()),
            option_right: Some("call".into()),
            status: "active".into(),
            ..Default::default()
        };
        snapshot
            .instruments
            .insert(instrument.instrument_id.clone(), instrument);
        let mut option_market = venue_market(
            "market:cboe-bzx-options:option:O:SPY260821C00500000",
            "instrument:option:SPY:20270115:500:C",
            "venue:cboe-bzx-options",
            "O:SPY260821C00500000",
        );
        option_market.origin_listing_id = Some(
            kairos_primitives::reference::ListingId::new(
                "listing:cboe-bzx-options:option:SPY-20270115-500-C",
            )
            .unwrap(),
        );
        snapshot.markets.push(option_market);
        let sources = BTreeMap::from([(
            "massive-options".into(),
            MarketProviderBinding::Massive {
                enabled: true,
                connection_id: None,
                product: MassiveMarketProduct::Options,
                credential_id: Some("massive".into()),
                endpoint: None,
            },
        )]);

        let update = resolve_market_universe(&snapshot, &sources).unwrap();

        assert_eq!(update.markets.len(), 1);
        let market = &update.markets[0];
        assert_eq!(
            market.member_id(),
            "market:cboe-bzx-options:option:O:SPY260821C00500000"
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
        let binding = market.runtime_route().unwrap();
        assert_eq!(binding.provider.as_str(), "massive");
        assert_eq!(binding.provider_segment, "options");
        assert_eq!(binding.subscription_symbol, "O:SPY260821C00500000");
        assert!(
            binding
                .observation_capabilities
                .contains(&crate::ObservationKind::Quote)
        );
        assert!(
            binding
                .observation_capabilities
                .contains(&crate::ObservationKind::Trade)
        );
    }

    fn fixture() -> kairos_reference_contract::MarketSearchResponse {
        let mut snapshot = kairos_reference_contract::MarketSearchResponse::default();
        snapshot.evidence = evidence();
        let instrument = kairos_reference_contract::Instrument {
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                .unwrap(),
            instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
            status: "active".into(),
            ..Default::default()
        };
        snapshot
            .instruments
            .insert(instrument.instrument_id.clone(), instrument);
        snapshot.markets.push(venue_market(
            "market:btc",
            "instrument:btc",
            "venue:binance",
            "BTCUSDT",
        ));
        snapshot
    }

    fn evidence() -> kairos_reference_contract::ReferenceQueryEvidence {
        kairos_reference_contract::ReferenceQueryEvidence {
            watermark: kairos_reference_contract::ReferenceWatermark {
                generation: 7.into(),
                event_sequence: 11.into(),
                ..Default::default()
            },
            conclusion: kairos_reference_contract::ReferenceKnowledgeConclusion::Found,
            ..Default::default()
        }
    }

    fn venue_market(
        market_id: &str,
        instrument_id: &str,
        execution_venue_id: &str,
        venue_symbol: &str,
    ) -> kairos_reference_contract::VenueMarket {
        kairos_reference_contract::VenueMarket {
            market_id: kairos_primitives::reference::MarketId::new(market_id).unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new(instrument_id).unwrap(),
            execution_venue_id: kairos_primitives::reference::VenueId::new(execution_venue_id)
                .unwrap(),
            origin_listing_id: None,
            market_segment_id: None,
            venue_symbol: Some(kairos_primitives::reference::Symbol::new(venue_symbol).unwrap()),
            trading_calendar_id: None,
            trading_session_ids: Vec::new(),
            base_asset_id: None,
            quote_asset_id: None,
            status: kairos_primitives::reference::ReferenceStatus::Active,
            trading_rules: kairos_reference_contract::TradingRules::default(),
            effective_from_unix_nanos: 0.into(),
            effective_to_unix_nanos: None,
        }
    }
}
