use std::collections::BTreeMap;

use crate::{MarketDataRoute, ReconcileMarketUniverse, ResolvedMarket};

pub(crate) fn project_market_universe(
    snapshot: &kairos_reference_contract::ReferenceProjectionSnapshot,
) -> Result<ReconcileMarketUniverse, String> {
    let instruments = snapshot
        .instruments
        .iter()
        .map(|instrument| (instrument.instrument_id.as_str(), instrument))
        .collect::<BTreeMap<_, _>>();
    let mut accesses = BTreeMap::<&str, Vec<&kairos_reference_contract::MarketDataAccess>>::new();
    for access in snapshot
        .market_data_accesses
        .iter()
        .filter(|access| is_active(&access.status))
    {
        accesses
            .entry(access.market_id.as_str())
            .or_default()
            .push(access);
    }

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
        let market_accesses = accesses
            .get(market.market_id.as_str())
            .map(Vec::as_slice)
            .unwrap_or_default();
        let access = match market_accesses {
            [access] => *access,
            [] => {
                return Err(format!(
                    "Reference market {} has no active market-data access",
                    market.market_id
                ))
            }
            _ => {
                return Err(format!(
                    "Reference market {} has ambiguous market-data accesses: {}",
                    market.market_id,
                    market_accesses
                        .iter()
                        .map(|access| access.access_id.as_str())
                        .collect::<Vec<_>>()
                        .join(",")
                ))
            }
        };
        let route = MarketDataRoute::new(
            access.access_id.clone(),
            access.provider_id.clone(),
            access.provider_product.clone(),
            access.provider_symbol.clone(),
        )?;
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
        markets.push(descriptor);
    }

    Ok(ReconcileMarketUniverse {
        generation: snapshot.generation.into(),
        event_sequence: snapshot.event_sequence.into(),
        markets,
    })
}

pub(super) fn project_market_universe_at_sequence(
    snapshot: &kairos_reference_contract::ReferenceProjectionSnapshot,
    required_sequence: u64,
) -> Result<ReconcileMarketUniverse, String> {
    if snapshot.event_sequence < required_sequence {
        return Err(format!(
            "Reference view sequence {} is behind required sequence {}",
            snapshot.event_sequence, required_sequence
        ));
    }
    project_market_universe(snapshot)
}

fn is_active(status: &str) -> bool {
    matches!(status, "active" | "trading")
}

#[cfg(test)]
mod tests {
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
            market_type: kairos_primitives::ProviderProductCode::new("spot").unwrap(),
            source_symbol: "BTCUSDT".into(),
            status: "active".into(),
            ..Default::default()
        });
        snapshot
            .market_data_accesses
            .push(kairos_reference_contract::MarketDataAccess {
                access_id: "access:btc".into(),
                market_id: "market:btc".into(),
                provider_id: "provider:binance".into(),
                provider_product: "spot".into(),
                provider_symbol: "BTCUSDT".into(),
                status: "active".into(),
                ..Default::default()
            });

        let update = project_market_universe(&snapshot).unwrap();
        assert_eq!(update.generation.get(), 7);
        assert_eq!(update.event_sequence.get(), 11);
        assert_eq!(update.markets.len(), 1);
        assert_eq!(update.markets[0].market_id, "market:btc");
        assert_eq!(update.markets[0].route.access_id, "access:btc");
        assert_eq!(update.markets[0].route.provider_id, "provider:binance");
        assert_eq!(update.markets[0].route.provider_product, "spot");
        assert_eq!(update.markets[0].route.provider_symbol, "BTCUSDT");
    }

    #[test]
    fn rejects_market_without_an_active_access() {
        let mut snapshot = fixture();
        snapshot.market_data_accesses.clear();
        let error = project_market_universe(&snapshot).unwrap_err();
        assert!(error.contains("has no active market-data access"));
    }

    #[test]
    fn rejects_market_with_ambiguous_active_accesses() {
        let mut snapshot = fixture();
        let mut second = snapshot.market_data_accesses[0].clone();
        second.access_id = "access:btc:second".into();
        snapshot.market_data_accesses.push(second);
        let error = project_market_universe(&snapshot).unwrap_err();
        assert!(error.contains("ambiguous market-data accesses"));
    }

    #[test]
    fn rejects_a_view_behind_the_required_event_sequence() {
        let snapshot = kairos_reference_contract::ReferenceProjectionSnapshot {
            event_sequence: 4,
            ..Default::default()
        };
        let error = project_market_universe_at_sequence(&snapshot, 5).unwrap_err();
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
            market_type: kairos_primitives::ProviderProductCode::new("spot").unwrap(),
            source_symbol: "BTCUSDT".into(),
            status: "active".into(),
            ..Default::default()
        });
        snapshot
            .market_data_accesses
            .push(kairos_reference_contract::MarketDataAccess {
                access_id: "access:btc".into(),
                market_id: "market:btc".into(),
                provider_id: "provider:binance".into(),
                provider_product: "spot".into(),
                provider_symbol: "BTCUSDT".into(),
                status: "active".into(),
                ..Default::default()
            });
        snapshot
    }
}
