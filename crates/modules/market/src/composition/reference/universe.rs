use std::collections::BTreeMap;

use crate::ReconcileMarketUniverse;
use crate::composition::config::MarketProviderBinding;
use crate::composition::sources::{binding_observation_capabilities, binding_provider_segment};

pub(crate) fn build_market_universe_resolver(
    sources: &BTreeMap<String, MarketProviderBinding>,
) -> crate::application::MarketUniverseResolver {
    use kairos_primitives::reference::InstrumentKind::{Equity, Future, Option, Perpetual, Spot};

    use crate::composition::config::{
        BinanceDerivativeProduct, HyperliquidMarketType, OkxInstrumentType,
    };

    let sources = sources
        .iter()
        .filter(|(_, binding)| binding.enabled())
        .map(|(_, binding)| {
            let (venue_id, instrument_kinds) = match binding {
                MarketProviderBinding::BinanceSpot { .. } => (Some("exchange:binance"), vec![Spot]),
                MarketProviderBinding::BinanceDerivatives { product, .. } => (
                    Some("exchange:binance"),
                    match product {
                        BinanceDerivativeProduct::Options => vec![Option],
                        BinanceDerivativeProduct::UsdMFutures
                        | BinanceDerivativeProduct::CoinMFutures => vec![Future, Perpetual],
                    },
                ),
                MarketProviderBinding::Okx {
                    instrument_type, ..
                } => (
                    Some("exchange:okx"),
                    vec![match instrument_type {
                        OkxInstrumentType::Spot => Spot,
                        OkxInstrumentType::Swap => Perpetual,
                        OkxInstrumentType::Futures => Future,
                        OkxInstrumentType::Options => Option,
                    }],
                ),
                MarketProviderBinding::Hyperliquid { market_type, .. } => (
                    Some("exchange:hyperliquid"),
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
                venue_id: venue_id
                    .map(kairos_primitives::reference::ExchangeId::new)
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
    snapshot: &kairos_reference_contract::MarketReferenceSnapshot,
    sources: &BTreeMap<String, MarketProviderBinding>,
) -> Result<ReconcileMarketUniverse, String> {
    build_market_universe_resolver(sources).resolve(snapshot, 0)
}

#[cfg(test)]
pub(super) fn resolve_market_universe_at_sequence(
    snapshot: &kairos_reference_contract::MarketReferenceSnapshot,
    required_sequence: u64,
    sources: &BTreeMap<String, MarketProviderBinding>,
) -> Result<ReconcileMarketUniverse, String> {
    build_market_universe_resolver(sources).resolve(snapshot, required_sequence)
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::{resolve_market_universe, resolve_market_universe_at_sequence};
    use crate::composition::config::{MarketProviderBinding, MassiveMarketProduct};

    #[test]
    fn maps_reference_view_to_market_owned_universe() {
        let mut snapshot = kairos_reference_contract::MarketReferenceSnapshot {
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
            exchange_id: kairos_primitives::reference::ExchangeId::new("exchange:binance").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
            venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
            status: "active".into(),
            ..Default::default()
        });
        let sources = BTreeMap::from([(
            "binance-spot-rest".into(),
            MarketProviderBinding::BinanceSpot {
                enabled: true,
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
        snapshot.markets[0].exchange_id =
            kairos_primitives::reference::ExchangeId::new("exchange:curated").unwrap();
        let update = resolve_market_universe(&snapshot, &BTreeMap::new()).unwrap();
        assert!(update.markets.is_empty());
    }

    #[test]
    fn equity_market_can_be_resolved_for_multiple_provider_sources() {
        let mut snapshot = kairos_reference_contract::MarketReferenceSnapshot {
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
            exchange_id: kairos_primitives::reference::ExchangeId::new("exchange:nasdaq").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Equity,
            asset_type: Some(kairos_primitives::reference::AssetClass::Equity),
            venue_symbol: Some(kairos_primitives::reference::Symbol::new("AAPL").unwrap()),
            status: "active".into(),
            ..Default::default()
        });
        let sources = BTreeMap::from([
            (
                "massive-equity".into(),
                MarketProviderBinding::Massive {
                    enabled: true,
                    product: MassiveMarketProduct::Equity,
                    credential_id: "massive".into(),
                    endpoint: None,
                },
            ),
            (
                "binance-equity".into(),
                MarketProviderBinding::BinanceEquity {
                    enabled: true,
                    credential_id: "binance".into(),
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
        let mut snapshot = kairos_reference_contract::MarketReferenceSnapshot {
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
            exchange_id: kairos_primitives::reference::ExchangeId::new("exchange:cboe-bzx-options")
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
            MarketProviderBinding::Massive {
                enabled: true,
                product: MassiveMarketProduct::Options,
                credential_id: "massive".into(),
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

    #[test]
    fn rejects_a_view_behind_the_required_event_sequence() {
        let snapshot = kairos_reference_contract::MarketReferenceSnapshot {
            event_sequence: 4.into(),
            ..Default::default()
        };
        let error =
            resolve_market_universe_at_sequence(&snapshot, 5, &BTreeMap::new()).unwrap_err();
        assert!(error.contains("behind required sequence 5"));
    }

    fn fixture() -> kairos_reference_contract::MarketReferenceSnapshot {
        let mut snapshot = kairos_reference_contract::MarketReferenceSnapshot::default();
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
            exchange_id: kairos_primitives::reference::ExchangeId::new("exchange:binance").unwrap(),
            instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
            venue_symbol: Some(kairos_primitives::reference::Symbol::new("BTCUSDT").unwrap()),
            status: "active".into(),
            ..Default::default()
        });
        snapshot
    }
}
