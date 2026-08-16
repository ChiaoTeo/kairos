//! Binance private order query connections for spot, futures and options.

use crate::services::participants::binance::ConnectionDomain;
use std::collections::BTreeMap;

use kairos_primitives::{ClientOrderId, OrderId, Symbol, UnixNanos};
use serde_json::Value;

use crate::application::capabilities::{DecimalValue, OrderSide, OrderType};
use crate::application::{
    ExternalOrder, ExternalOrderQuery, IntegrationError, OrderQueryConnection,
};

use super::futures::account::BinanceFuturesAccountClient;
use super::options::account::{BinanceOptionsAccountClient, Method as OptionsMethod};
use super::spot::account::BinanceSpotAccountClient;

enum Client {
    Spot(BinanceSpotAccountClient),
    Futures(BinanceFuturesAccountClient),
    Options(BinanceOptionsAccountClient),
}

impl Client {
    fn open_orders(&self, query: &ExternalOrderQuery) -> Result<Value, IntegrationError> {
        match self {
            Self::Spot(client) => client
                .query_open_orders(params(query))
                .map_err(map_spot_error),
            Self::Futures(client) => client
                .query_open_orders(params(query))
                .map_err(|error| IntegrationError::Transport(error.to_string())),
            Self::Options(client) => client
                .request("/eapi/v1/openOrders", params(query), OptionsMethod::Get)
                .map_err(|error| IntegrationError::Transport(error.to_string())),
        }
    }
    fn history(&self, query: &ExternalOrderQuery) -> Result<Value, IntegrationError> {
        match self {
            Self::Spot(client) => client.query_history(params(query)).map_err(map_spot_error),
            Self::Futures(client) => client
                .query_history(params(query))
                .map_err(|error| IntegrationError::Transport(error.to_string())),
            Self::Options(client) => client
                .request("/eapi/v1/historyOrders", params(query), OptionsMethod::Get)
                .map_err(|error| IntegrationError::Transport(error.to_string())),
        }
    }
    fn detail(&self, query: &ExternalOrderQuery) -> Result<Value, IntegrationError> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| IntegrationError::InvalidRequest("order_id is required".into()))?;
        let mut values = params(query);
        values.insert("orderId".into(), order_id.into());
        match self {
            Self::Spot(client) => client.query_detail(values).map_err(map_spot_error),
            Self::Futures(client) => client
                .query_detail(values)
                .map_err(|error| IntegrationError::Transport(error.to_string())),
            Self::Options(client) => client
                .request("/eapi/v1/order", values, OptionsMethod::Get)
                .map_err(|error| IntegrationError::Transport(error.to_string())),
        }
    }
}

fn map_spot_error(error: crate::services::transport::http::ExchangeError) -> IntegrationError {
    use crate::services::transport::http::ExchangeError;
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::LocalRateLimit { message, .. } => IntegrationError::RateLimited(message),
        ExchangeError::Http { status: 401, body } => IntegrationError::Authentication(body),
        ExchangeError::Http { status: 403, body } => IntegrationError::Authorization(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}

pub struct BinanceOrderQueryConnection {
    client: Client,
}

impl BinanceOrderQueryConnection {
    pub fn new(
        product: ConnectionDomain,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = match product {
            ConnectionDomain::Spot => Client::Spot(
                BinanceSpotAccountClient::new(api_key, secret, base_url)
                    .map_err(|e| e.to_string())?,
            ),
            ConnectionDomain::UsdMFutures | ConnectionDomain::CoinMFutures => Client::Futures(
                BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
                    .map_err(|e| e.to_string())?,
            ),
            ConnectionDomain::Options => Client::Options(
                BinanceOptionsAccountClient::new(api_key, secret, base_url)
                    .map_err(|e| e.to_string())?,
            ),
            _ => {
                return Err("Binance order query requires spot, futures, or options product".into())
            }
        };
        Ok(Self { client })
    }

    pub(crate) fn spot_from_client(client: BinanceSpotAccountClient) -> Result<Self, String> {
        Ok(Self {
            client: Client::Spot(client),
        })
    }

    pub(crate) fn futures_from_client(client: BinanceFuturesAccountClient) -> Self {
        Self {
            client: Client::Futures(client),
        }
    }

    pub(crate) fn options_from_client(client: BinanceOptionsAccountClient) -> Self {
        Self {
            client: Client::Options(client),
        }
    }

    pub(crate) async fn open_orders_async(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let payload = match &self.client {
            Client::Spot(client) => client
                .query_open_orders_async(params(query))
                .await
                .map_err(map_spot_error)?,
            Client::Futures(client) => client
                .query_open_orders_async(params(query))
                .await
                .map_err(map_spot_error)?,
            Client::Options(client) => client
                .request_async("/eapi/v1/openOrders", params(query), OptionsMethod::Get)
                .await
                .map_err(map_spot_error)?,
        };
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }

    pub(crate) async fn order_history_async(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let payload = match &self.client {
            Client::Spot(client) => client
                .query_history_async(params(query))
                .await
                .map_err(map_spot_error)?,
            Client::Futures(client) => client
                .query_history_async(params(query))
                .await
                .map_err(map_spot_error)?,
            Client::Options(client) => client
                .request_async("/eapi/v1/historyOrders", params(query), OptionsMethod::Get)
                .await
                .map_err(map_spot_error)?,
        };
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }

    pub(crate) async fn order_detail_async(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| IntegrationError::InvalidRequest("order_id is required".into()))?;
        let mut values = params(query);
        values.insert("orderId".into(), order_id.into());
        let value = match &self.client {
            Client::Spot(client) => client
                .query_detail_async(values)
                .await
                .map_err(map_spot_error)?,
            Client::Futures(client) => client
                .query_detail_async(values)
                .await
                .map_err(map_spot_error)?,
            Client::Options(client) => client
                .request_async("/eapi/v1/order", values, OptionsMethod::Get)
                .await
                .map_err(map_spot_error)?,
        };
        if value.is_null() {
            Ok(None)
        } else {
            normalize_one(&value)
                .map(Some)
                .map_err(IntegrationError::InvalidPayload)
        }
    }
}

impl OrderQueryConnection for BinanceOrderQueryConnection {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let payload = self.client.open_orders(query)?;
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }
    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let payload = self.client.history(query)?;
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }
    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let value = self.client.detail(query)?;
        if value.is_null() {
            Ok(None)
        } else {
            normalize_one(&value)
                .map(Some)
                .map_err(IntegrationError::InvalidPayload)
        }
    }
}

pub(super) fn params(query: &ExternalOrderQuery) -> BTreeMap<String, String> {
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

pub(super) fn normalize_many(value: &Value) -> Result<Vec<ExternalOrder>, String> {
    let rows = value
        .as_array()
        .ok_or_else(|| "Binance order query returned a non-array payload".to_string())?;
    rows.iter().map(normalize_one).collect()
}

pub(super) fn normalize_one(value: &Value) -> Result<ExternalOrder, String> {
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
        binding_id: String::new(),
        order_id: OrderId::try_from(order_id).map_err(|error| error.to_string())?,
        client_order_id: text("clientOrderId")
            .or_else(|| text("clientOrderID"))
            .map(ClientOrderId::try_from)
            .transpose()
            .map_err(|error| error.to_string())?,
        symbol: Symbol::try_from(text("symbol").unwrap_or_else(|| "UNKNOWN".into()))
            .map_err(|error| error.to_string())?,
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
        status: crate::application::capabilities::execution_facts::normalize_order_status(
            &text("status").unwrap_or_else(|| text("state").unwrap_or_else(|| "UNKNOWN".into())),
        ),
        quantity,
        filled_quantity,
        average_fill_price,
        occurred_at_unix_millis: row
            .get("updateTime")
            .or_else(|| row.get("time"))
            .and_then(Value::as_u64)
            .map(|value| UnixNanos::from(value.saturating_mul(1_000_000))),
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
    DecimalValue::parse(&text)
}
fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
