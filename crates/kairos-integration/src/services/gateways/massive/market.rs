//! Massive market/reference gateway.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceDataConnection, ReferenceEntity,
    ReferenceInstrument, ReferenceListing, ReferenceMarket,
};
use crate::application::{Connection, ConnectionSpec};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MassiveMarketRow {
    pub ticker: String,
    pub market_type: String,
    pub base: Option<String>,
    pub quote: Option<String>,
    pub active: bool,
    pub price_tick: Option<String>,
    pub amount_tick: Option<String>,
    pub price_precision: i32,
    pub amount_precision: i32,
    pub underlying: Option<String>,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub contract_size: Option<String>,
}

pub trait MassiveMarketClient: Send {
    fn load_markets(&mut self) -> Result<Vec<MassiveMarketRow>, String>;
}

pub struct MassiveReferenceConnection<C> {
    connection: ManagedConnection,
    client: C,
}

impl<C: MassiveMarketClient> MassiveReferenceConnection<C> {
    pub fn open(client: C) -> Result<Self, String> {
        let spec = ConnectionSpec {
            connection_id: "reference.massive.market".into(),
            provider: "massive".into(),
            product: Some(ProductFamily::Equity),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: Some(crate::domain::AssetType::Equity),
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }
}

impl<C: MassiveMarketClient> Connection for MassiveReferenceConnection<C> {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.connection.reconnect()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl<C: MassiveMarketClient> ReferenceDataConnection for MassiveReferenceConnection<C> {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String> {
        self.start()?;
        normalize(self.client.load_markets()?)
    }
}

pub fn normalize(rows: Vec<MassiveMarketRow>) -> Result<ReferenceCatalogPayload, String> {
    let now = now_unix_nanos();
    let mut assets = BTreeMap::new();
    let mut result = ReferenceCatalogPayload {
        entities: vec![ReferenceEntity {
            entity_id: "massive".into(),
            entity_type: "venue".into(),
            name: "Massive".into(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    for row in rows {
        if row.ticker.trim().is_empty() {
            return Err("Massive ticker is required".into());
        }
        let market_type = if row.market_type.trim().is_empty() {
            "equity".to_string()
        } else {
            row.market_type.to_ascii_lowercase()
        };
        let asset_class = if market_type == "equity" {
            "equity"
        } else {
            "crypto"
        };
        let base_asset_id = row.base.as_ref().map(|base| {
            let id = format!("asset:{asset_class}:{}", base.to_ascii_uppercase());
            assets.entry(id.clone()).or_insert_with(|| ReferenceAsset {
                asset_id: id.clone(),
                code: base.to_ascii_uppercase(),
                asset_class: asset_class.into(),
                status: "active".into(),
            });
            id
        });
        let quote_asset_id = row.quote.as_ref().map(|quote| {
            let id = format!("asset:fiat:{}", quote.to_ascii_uppercase());
            assets.entry(id.clone()).or_insert_with(|| ReferenceAsset {
                asset_id: id.clone(),
                code: quote.to_ascii_uppercase(),
                asset_class: "fiat".into(),
                status: "active".into(),
            });
            id
        });
        let instrument_id = format!("instrument:massive:{}", row.ticker);
        let listing_id = format!("listing:massive:{}", row.ticker);
        let status = if row.active { "active" } else { "inactive" };
        result.instruments.push(ReferenceInstrument {
            instrument_id: instrument_id.clone(),
            symbol: row.ticker.clone(),
            instrument_type: market_type.clone(),
            product_family: Some(market_type.clone()),
            underlying_instrument_id: row
                .underlying
                .as_ref()
                .map(|value| format!("instrument:massive:{value}")),
            expiry_unix_nanos: row.expiry_unix_nanos,
            strike: row.strike,
            option_right: row.option_right,
            status: status.into(),
        });
        result.listings.push(ReferenceListing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            venue_id: "massive".into(),
            venue_symbol: row.ticker.clone(),
            status: status.into(),
            effective_from_unix_nanos: now,
        });
        result.markets.push(ReferenceMarket {
            market_id: format!("market:massive:{}", row.ticker),
            market_key: format!("massive.{market_type}.{}", row.ticker),
            instrument_id,
            listing_id,
            venue_id: "massive".into(),
            market_type,
            source_symbol: row.ticker,
            base_asset_id,
            quote_asset_id,
            status: status.into(),
            price_tick: row.price_tick,
            quantity_tick: row.amount_tick,
            price_precision: row.price_precision,
            quantity_precision: row.amount_precision,
            minimum_quantity: None,
            minimum_notional: None,
            contract_size: row.contract_size,
            effective_to_unix_nanos: row.expiry_unix_nanos,
        });
    }
    result.assets = assets.into_values().collect();
    Ok(result)
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::{normalize, MassiveMarketClient, MassiveMarketRow, MassiveReferenceConnection};
    use crate::application::reference::ReferenceDataConnection;
    use crate::application::Connection;

    struct FakeMassive;

    impl MassiveMarketClient for FakeMassive {
        fn load_markets(&mut self) -> Result<Vec<MassiveMarketRow>, String> {
            Ok(vec![MassiveMarketRow {
                ticker: "O:SPY260821C00500000".into(),
                market_type: "option".into(),
                base: Some("SPY".into()),
                quote: Some("USD".into()),
                active: true,
                price_tick: Some("0.01".into()),
                amount_tick: Some("1".into()),
                price_precision: 2,
                amount_precision: 0,
                underlying: Some("SPY".into()),
                expiry_unix_nanos: Some(1_800_000_000_000_000_000),
                strike: Some("500".into()),
                option_right: Some("call".into()),
                contract_size: Some("100".into()),
            }])
        }
    }

    #[test]
    fn massive_option_rows_normalize_through_the_reference_connection() {
        let mut connection = MassiveReferenceConnection::open(FakeMassive).unwrap();
        let payload = connection.fetch_reference_catalog().unwrap();
        assert!(connection.health().healthy);
        assert_eq!(payload.instruments[0].option_right.as_deref(), Some("call"));
        assert_eq!(payload.instruments[0].strike.as_deref(), Some("500"));
        assert_eq!(payload.markets[0].contract_size.as_deref(), Some("100"));
    }

    #[test]
    fn normalize_rejects_missing_ticker() {
        let error = normalize(vec![MassiveMarketRow {
            ticker: String::new(),
            market_type: "equity".into(),
            base: None,
            quote: None,
            active: true,
            price_tick: None,
            amount_tick: None,
            price_precision: 0,
            amount_precision: 0,
            underlying: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            contract_size: None,
        }])
        .unwrap_err();
        assert!(error.contains("ticker"));
    }
}
