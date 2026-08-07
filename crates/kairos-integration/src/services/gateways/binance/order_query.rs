//! Binance private order query connections for spot, futures and options.

use std::collections::BTreeMap;

use serde_json::Value;

use crate::application::{
    Connection, ConnectionSpec, ExternalOrder, ExternalOrderQuery, OrderQueryConnection,
};
use crate::domain::{
    AccessScope, DecimalValue, IntegrationCapability, OrderSide, OrderType, ProductFamily,
    TransportKind,
};
use crate::services::connections::ManagedConnection;

use super::futures::account::BinanceFuturesAccountClient;
use super::options::account::{BinanceOptionsAccountClient, Method as OptionsMethod};
use super::spot::account::BinanceSpotAccountClient;

enum Client {
    Spot(BinanceSpotAccountClient),
    Futures(BinanceFuturesAccountClient),
    Options(BinanceOptionsAccountClient),
}

impl Client {
    fn open_orders(&self, query: &ExternalOrderQuery) -> Result<Value, String> {
        match self {
            Self::Spot(client) => client
                .query_open_orders(params(query))
                .map_err(|e| e.to_string()),
            Self::Futures(client) => client
                .query_open_orders(params(query))
                .map_err(|e| e.to_string()),
            Self::Options(client) => client
                .request("/eapi/v1/openOrders", params(query), OptionsMethod::Get)
                .map_err(|e| e.to_string()),
        }
    }
    fn history(&self, query: &ExternalOrderQuery) -> Result<Value, String> {
        match self {
            Self::Spot(client) => client
                .query_history(params(query))
                .map_err(|e| e.to_string()),
            Self::Futures(client) => client
                .query_history(params(query))
                .map_err(|e| e.to_string()),
            Self::Options(client) => client
                .request("/eapi/v1/historyOrders", params(query), OptionsMethod::Get)
                .map_err(|e| e.to_string()),
        }
    }
    fn detail(&self, query: &ExternalOrderQuery) -> Result<Value, String> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| "order_id is required".to_string())?;
        let mut values = params(query);
        values.insert("orderId".into(), order_id.into());
        match self {
            Self::Spot(client) => client.query_detail(values).map_err(|e| e.to_string()),
            Self::Futures(client) => client.query_detail(values).map_err(|e| e.to_string()),
            Self::Options(client) => client
                .request("/eapi/v1/order", values, OptionsMethod::Get)
                .map_err(|e| e.to_string()),
        }
    }
}

pub struct BinanceOrderQueryConnection {
    connection: ManagedConnection,
    client: Client,
}

impl BinanceOrderQueryConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = match product {
            ProductFamily::Spot => Client::Spot(
                BinanceSpotAccountClient::new(api_key, secret, base_url)
                    .map_err(|e| e.to_string())?,
            ),
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures => Client::Futures(
                BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
                    .map_err(|e| e.to_string())?,
            ),
            ProductFamily::Options => Client::Options(
                BinanceOptionsAccountClient::new(api_key, secret, base_url)
                    .map_err(|e| e.to_string())?,
            ),
            _ => {
                return Err("Binance order query requires spot, futures, or options product".into())
            }
        };
        let connection = ManagedConnection::new(
            ConnectionSpec {
                connection_id: format!(
                    "execution.binance.{}.order-read.rest",
                    product_name(product)
                ),
                provider: "binance".into(),
                product: Some(product),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::OrderRead,
                credential_id: Some("binance".into()),
                asset_type: None,
            },
            Vec::new(),
        )?;
        Ok(Self { connection, client })
    }
}

impl Connection for BinanceOrderQueryConnection {
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

impl OrderQueryConnection for BinanceOrderQueryConnection {
    fn open_orders(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        self.start()?;
        normalize_many(&self.client.open_orders(query)?)
    }
    fn order_history(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        self.start()?;
        normalize_many(&self.client.history(query)?)
    }
    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, String> {
        self.start()?;
        let value = self.client.detail(query)?;
        if value.is_null() {
            Ok(None)
        } else {
            normalize_one(&value).map(Some)
        }
    }
}

fn params(query: &ExternalOrderQuery) -> BTreeMap<String, String> {
    let mut result = BTreeMap::new();
    if let Some(symbol) = &query.symbol {
        result.insert("symbol".into(), symbol.to_ascii_uppercase());
    }
    if let Some(limit) = query.limit {
        result.insert("limit".into(), limit.to_string());
    }
    if let Some(since) = query.since_unix_millis {
        result.insert("startTime".into(), since.to_string());
    }
    result
}

fn normalize_many(value: &Value) -> Result<Vec<ExternalOrder>, String> {
    let rows = value
        .as_array()
        .ok_or_else(|| "Binance order query returned a non-array payload".to_string())?;
    rows.iter().map(normalize_one).collect()
}

fn normalize_one(value: &Value) -> Result<ExternalOrder, String> {
    let row = value
        .as_object()
        .ok_or_else(|| "Binance order row is not an object".to_string())?;
    let text = |key: &str| {
        row.get(key)
            .map(value_string)
            .filter(|value| !value.is_empty())
    };
    let order_id = text("orderId").ok_or_else(|| "Binance order id is missing".to_string())?;
    let quantity = decimal(
        row.get("origQty")
            .or_else(|| row.get("quantity"))
            .unwrap_or(&Value::Null),
    )?;
    let filled_quantity = decimal(
        row.get("executedQty")
            .or_else(|| row.get("filledQty"))
            .unwrap_or(&Value::Null),
    )?;
    let average_fill_price = row
        .get("avgPrice")
        .or_else(|| row.get("price"))
        .filter(|v| !v.is_null())
        .map(decimal)
        .transpose()?;
    Ok(ExternalOrder {
        order_id,
        client_order_id: text("clientOrderId").or_else(|| text("clientOrderID")),
        symbol: text("symbol").unwrap_or_else(|| "UNKNOWN".into()),
        side: if text("side").as_deref() == Some("SELL") {
            OrderSide::Sell
        } else {
            OrderSide::Buy
        },
        order_type: if matches!(text("type").as_deref(), Some("MARKET") | Some("market")) {
            OrderType::Market
        } else {
            OrderType::Limit
        },
        status: text("status").unwrap_or_else(|| text("state").unwrap_or_else(|| "UNKNOWN".into())),
        quantity,
        filled_quantity,
        average_fill_price,
        occurred_at_unix_millis: row
            .get("updateTime")
            .or_else(|| row.get("time"))
            .and_then(Value::as_u64),
    })
}

fn decimal(value: &Value) -> Result<DecimalValue, String> {
    let text = value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string());
    if text == "null" || text.is_empty() {
        return Ok(DecimalValue::new(0, 0));
    }
    let negative = text.starts_with('-');
    let unsigned = text.trim_start_matches('-');
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    let mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| format!("invalid decimal: {text}"))?;
    Ok(DecimalValue::new(
        if negative { -mantissa } else { mantissa },
        fraction.len() as u8,
    ))
}
fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
fn product_name(product: ProductFamily) -> &'static str {
    match product {
        ProductFamily::Spot => "spot",
        ProductFamily::UsdMFutures => "usd-m-futures",
        ProductFamily::CoinMFutures => "coin-m-futures",
        ProductFamily::Options => "options",
        _ => "unknown",
    }
}
