//! CCXT market/reference gateway.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceDataConnection, ReferenceEntity,
    ReferenceInstrument, ReferenceListing, ReferenceMarket,
};
use crate::application::Connection;
use crate::application::ConnectionSpec;
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;
use crate::services::factories::{GatewaySelector, IntegrationConnectionFactory};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CcxtMarketRow {
    pub id: String,
    pub symbol: String,
    pub base: Option<String>,
    pub quote: Option<String>,
    pub market_type: String,
    pub active: bool,
    pub price_tick: Option<String>,
    pub amount_tick: Option<String>,
    pub price_precision: i32,
    pub amount_precision: i32,
    pub contract_size: Option<String>,
}

pub trait CcxtMarketClient: Send + Sync {
    fn exchange_id(&self) -> &str;
    fn load_markets(&mut self) -> Result<Vec<CcxtMarketRow>, String>;
}

pub struct CcxtReferenceConnection<C> {
    connection: ManagedConnection,
    client: C,
}

impl<C: CcxtMarketClient> CcxtReferenceConnection<C> {
    pub fn open(client: C) -> Result<Self, String> {
        let exchange = client.exchange_id().trim().to_ascii_lowercase();
        if exchange.is_empty() {
            return Err("CCXT exchange id is required".into());
        }
        let spec = ConnectionSpec {
            connection_id: format!("reference.ccxt.{exchange}.market"),
            provider: "ccxt".into(),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: Some(crate::domain::AssetType::Crypto),
        };
        let connection = ManagedConnection::new(spec, Vec::new())?;
        Ok(Self { connection, client })
    }
}

impl<C: CcxtMarketClient> Connection for CcxtReferenceConnection<C> {
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

impl<C: CcxtMarketClient> ReferenceDataConnection for CcxtReferenceConnection<C> {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String> {
        self.start()?;
        let exchange = self.client.exchange_id().trim().to_ascii_lowercase();
        let rows = self.client.load_markets()?;
        normalize(exchange, rows)
    }
}

pub struct CcxtReferenceFactory<C> {
    client: Option<C>,
}

impl<C> CcxtReferenceFactory<C> {
    pub fn new(client: C) -> Self {
        Self {
            client: Some(client),
        }
    }
}

impl<C: CcxtMarketClient + Clone + Sync + 'static> IntegrationConnectionFactory
    for CcxtReferenceFactory<C>
{
    fn connect(&self, spec: &ConnectionSpec) -> Result<Box<dyn Connection>, String> {
        if !self.supports(spec) {
            return Err("CCXT reference factory does not support this connection spec".into());
        }
        let client = self
            .client
            .as_ref()
            .ok_or_else(|| "CCXT reference factory has already been consumed".to_string())?;
        Ok(Box::new(CcxtReferenceConnection::open(client.clone())?))
    }
}

impl<C: CcxtMarketClient + Clone + Sync + 'static> GatewaySelector for CcxtReferenceFactory<C> {
    fn supports(&self, spec: &ConnectionSpec) -> bool {
        spec.provider == "ccxt"
            && spec.product == Some(ProductFamily::Spot)
            && spec.access == AccessScope::Public
            && spec.transport == TransportKind::Rest
            && spec.capability == IntegrationCapability::Reference
    }
}

pub fn normalize(
    exchange: String,
    rows: Vec<CcxtMarketRow>,
) -> Result<ReferenceCatalogPayload, String> {
    if exchange.is_empty() {
        return Err("CCXT exchange id is required".into());
    }
    let now = now_unix_nanos();
    let mut assets = BTreeMap::new();
    let mut result = ReferenceCatalogPayload {
        entities: vec![ReferenceEntity {
            entity_id: format!("ccxt:{exchange}"),
            entity_type: "venue".into(),
            name: exchange.clone(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    for row in rows {
        if row.id.trim().is_empty() || row.symbol.trim().is_empty() {
            return Err("CCXT market id and symbol are required".into());
        }
        let market_type = if row.market_type.trim().is_empty() {
            "spot".to_string()
        } else {
            row.market_type.to_ascii_lowercase()
        };
        let base_asset_id = row.base.as_ref().map(|base| {
            let id = format!("asset:crypto:{}", base.to_ascii_uppercase());
            assets.entry(id.clone()).or_insert_with(|| ReferenceAsset {
                asset_id: id.clone(),
                code: base.to_ascii_uppercase(),
                asset_class: "crypto".into(),
                status: "active".into(),
            });
            id
        });
        let quote_asset_id = row.quote.as_ref().map(|quote| {
            let id = format!("asset:crypto:{}", quote.to_ascii_uppercase());
            assets.entry(id.clone()).or_insert_with(|| ReferenceAsset {
                asset_id: id.clone(),
                code: quote.to_ascii_uppercase(),
                asset_class: "crypto".into(),
                status: "active".into(),
            });
            id
        });
        let instrument_id = format!("instrument:ccxt:{exchange}:{}", row.id);
        let listing_id = format!("listing:ccxt:{exchange}:{}", row.id);
        result.instruments.push(ReferenceInstrument {
            instrument_id: instrument_id.clone(),
            symbol: row.symbol.clone(),
            instrument_type: market_type.clone(),
            product_family: Some(market_type.clone()),
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            status: if row.active { "active" } else { "inactive" }.into(),
        });
        result.listings.push(ReferenceListing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            venue_id: format!("ccxt:{exchange}"),
            venue_symbol: row.symbol.clone(),
            status: if row.active { "active" } else { "inactive" }.into(),
            effective_from_unix_nanos: now,
        });
        result.markets.push(ReferenceMarket {
            market_id: format!("market:ccxt:{exchange}:{}", row.id),
            market_key: format!("ccxt.{exchange}.{}", row.id),
            instrument_id,
            listing_id,
            venue_id: format!("ccxt:{exchange}"),
            market_type,
            source_symbol: row.symbol,
            base_asset_id,
            quote_asset_id,
            status: if row.active { "active" } else { "inactive" }.into(),
            price_tick: row.price_tick,
            quantity_tick: row.amount_tick,
            price_precision: row.price_precision,
            quantity_precision: row.amount_precision,
            minimum_quantity: None,
            minimum_notional: None,
            contract_size: row.contract_size,
            effective_to_unix_nanos: None,
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
    use super::{normalize, CcxtMarketClient, CcxtMarketRow, CcxtReferenceConnection};
    use crate::application::reference::ReferenceDataConnection;
    use crate::application::Connection;

    struct FakeClient;

    impl CcxtMarketClient for FakeClient {
        fn exchange_id(&self) -> &str {
            "demo"
        }
        fn load_markets(&mut self) -> Result<Vec<CcxtMarketRow>, String> {
            Ok(vec![CcxtMarketRow {
                id: "BTC/USDT".into(),
                symbol: "BTC/USDT".into(),
                base: Some("BTC".into()),
                quote: Some("USDT".into()),
                market_type: "spot".into(),
                active: true,
                price_tick: Some("0.01".into()),
                amount_tick: Some("0.000001".into()),
                price_precision: 2,
                amount_precision: 6,
                contract_size: None,
            }])
        }
    }

    #[test]
    fn ccxt_connection_normalizes_market_rows_and_owns_lifecycle() {
        let mut connection = CcxtReferenceConnection::open(FakeClient).unwrap();
        let payload = connection.fetch_reference_catalog().unwrap();
        assert!(connection.health().healthy);
        assert_eq!(payload.markets[0].market_id, "market:ccxt:demo:BTC/USDT");
        assert_eq!(payload.assets.len(), 2);
    }

    #[test]
    fn normalize_rejects_incomplete_rows() {
        let error = normalize(
            "demo".into(),
            vec![CcxtMarketRow {
                id: String::new(),
                symbol: "BTC/USDT".into(),
                base: None,
                quote: None,
                market_type: "spot".into(),
                active: true,
                price_tick: None,
                amount_tick: None,
                price_precision: 0,
                amount_precision: 0,
                contract_size: None,
            }],
        )
        .unwrap_err();
        assert!(error.contains("market id"));
    }
}
