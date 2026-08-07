use std::collections::BTreeMap;
use std::net::TcpStream;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::domain::account::{
    ExternalAccountEvent as AccountEvent, ExternalAccountModel as AccountModel,
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalFillEvent as FillEvent,
    ExternalOpenOrder as OpenOrder, ExternalOrderEvent as OrderEvent,
    ExternalOrderStatus as OrderStatus, ExternalPosition as Position,
};
use serde_json::Value;
use tungstenite::{connect, Message, WebSocket};

use crate::application::{
    AccountCredentialInspectionConnection, AccountEventStreamConnection, AccountReadConnection,
    Connection, ConnectionSpec, ExternalAccountCredentialProfile, IntegrationError,
};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::auth::signed_query;
use crate::services::connections::ManagedConnection;
use crate::services::drivers::http::{ExchangeError, PublicHttpClient};

pub struct BinanceFuturesAccountConnection {
    connection: ManagedConnection,
    client: BinanceFuturesAccountClient,
}

impl BinanceFuturesAccountConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
        ) {
            return Err("Binance futures account requires USD-M or Coin-M product".into());
        }
        let client = BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: format!("account.binance.{}.rest", product_name(product)),
            provider: "binance".into(),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountRead,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        let connection = ManagedConnection::new(spec, Vec::new())?;
        Ok(Self { connection, client })
    }
}

impl Connection for BinanceFuturesAccountConnection {
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

impl AccountReadConnection for BinanceFuturesAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let account = self
            .client
            .account()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let positions = self
            .client
            .positions()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let orders = self
            .client
            .open_orders()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_account(segment, &account, &positions, &orders)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for BinanceFuturesAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        self.start()?;
        let payload = self.client.account().map_err(|error| error.to_string())?;
        let account_type = payload
            .get("accountType")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let mut permissions = vec!["read".into()];
        if payload
            .get("canTrade")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            permissions.push("trade".into());
        }
        let segment = match self.client.product {
            ProductFamily::UsdMFutures => "usd_m_futures",
            ProductFamily::CoinMFutures => "coin_m_futures",
            _ => "futures",
        };
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type,
            permissions,
            segments: vec![segment.into()],
            attributes: Default::default(),
        })
    }
}

pub(crate) struct BinanceFuturesAccountClient {
    http: PublicHttpClient,
    product: ProductFamily,
    api_key: String,
    secret: String,
    base_url: String,
}

impl BinanceFuturesAccountClient {
    pub(crate) fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if api_key.trim().is_empty() || secret.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance futures credentials are required".into(),
            ));
        }
        if base_url.is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance futures base URL is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/binance-futures-account")?,
            product,
            api_key,
            secret,
            base_url,
        })
    }

    fn signed_get(&self, path: &str) -> Result<Value, ExchangeError> {
        self.signed_request(path, BTreeMap::new(), RequestMethod::Get)
    }

    pub(super) fn submit_order(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(self.order_path(), params, RequestMethod::Post)
    }

    pub(super) fn cancel_order(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(self.order_path(), params, RequestMethod::Delete)
    }

    pub(super) fn listen_key(&self) -> Result<String, ExchangeError> {
        let path = match self.product {
            ProductFamily::UsdMFutures => "/fapi/v1/listenKey",
            ProductFamily::CoinMFutures => "/dapi/v1/listenKey",
            _ => unreachable!(),
        };
        let endpoint = format!("{}{path}", self.base_url);
        self.http
            .post_json_with_headers_and_query(
                &endpoint,
                &[],
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )?
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| {
                ExchangeError::InvalidRequest("Binance futures listen key is missing".into())
            })
    }

    fn order_path(&self) -> &'static str {
        match self.product {
            ProductFamily::UsdMFutures => "/fapi/v1/order",
            ProductFamily::CoinMFutures => "/dapi/v1/order",
            _ => unreachable!(),
        }
    }

    fn signed_request(
        &self,
        path: &str,
        mut params: BTreeMap<String, String>,
        method: RequestMethod,
    ) -> Result<Value, ExchangeError> {
        params.insert("timestamp".into(), now_millis().to_string());
        params.insert("recvWindow".into(), "10000".into());
        let signed = signed_query(&self.secret, params.into_iter())?;
        let endpoint = format!("{}{path}", self.base_url);
        let mut query = url::form_urlencoded::parse(signed.query.as_bytes())
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect::<Vec<_>>();
        query.push(("signature".into(), signed.signature));
        let refs = query
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect::<Vec<_>>();
        let headers = [("X-MBX-APIKEY", self.api_key.clone())];
        match method {
            RequestMethod::Get => self
                .http
                .get_json_with_headers_and_query(&endpoint, &refs, &headers),
            RequestMethod::Post => self
                .http
                .post_json_with_headers_and_query(&endpoint, &refs, &headers),
            RequestMethod::Delete => self
                .http
                .delete_json_with_headers_and_query(&endpoint, &refs, &headers),
        }
    }

    fn account(&self) -> Result<Value, ExchangeError> {
        self.signed_get(match self.product {
            ProductFamily::UsdMFutures => "/fapi/v2/account",
            ProductFamily::CoinMFutures => "/dapi/v1/account",
            _ => unreachable!(),
        })
    }

    fn positions(&self) -> Result<Value, ExchangeError> {
        self.signed_get(match self.product {
            ProductFamily::UsdMFutures => "/fapi/v2/positionRisk",
            ProductFamily::CoinMFutures => "/dapi/v1/positionRisk",
            _ => unreachable!(),
        })
    }

    fn open_orders(&self) -> Result<Value, ExchangeError> {
        self.signed_request(
            match self.product {
                ProductFamily::UsdMFutures => "/fapi/v1/openOrders",
                ProductFamily::CoinMFutures => "/dapi/v1/openOrders",
                _ => unreachable!(),
            },
            BTreeMap::new(),
            RequestMethod::Get,
        )
    }

    pub(crate) fn query_open_orders(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(
            match self.product {
                ProductFamily::UsdMFutures => "/fapi/v1/openOrders",
                ProductFamily::CoinMFutures => "/dapi/v1/openOrders",
                _ => unreachable!(),
            },
            params,
            RequestMethod::Get,
        )
    }
    pub(crate) fn query_history(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(
            match self.product {
                ProductFamily::UsdMFutures => "/fapi/v1/allOrders",
                ProductFamily::CoinMFutures => "/dapi/v1/allOrders",
                _ => unreachable!(),
            },
            params,
            RequestMethod::Get,
        )
    }
    pub(crate) fn query_detail(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(self.order_path(), params, RequestMethod::Get)
    }
}

pub(super) enum RequestMethod {
    Get,
    Post,
    Delete,
}

fn normalize_account(
    segment: &AccountSegment,
    account: &Value,
    positions: &Value,
    orders: &Value,
) -> Result<AccountSnapshot, String> {
    let assets = account
        .get("assets")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance futures account assets is missing".to_string())?;
    let balances = assets
        .iter()
        .map(|item| {
            let code = item
                .get("asset")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance futures asset is missing".to_string())?;
            let wallet = required_decimal_field(item, "walletBalance")?;
            let available = required_decimal_field(item, "availableBalance").ok();
            Ok(Balance {
                asset_id: format!("asset:crypto:{code}"),
                asset_code: code.into(),
                total: wallet,
                available,
                ..Default::default()
            })
        })
        .collect::<Result<Vec<_>, String>>()?;

    let rows = positions
        .as_array()
        .ok_or_else(|| "Binance futures positions is not an array".to_string())?;
    let positions = rows
        .iter()
        .filter_map(|item| {
            let symbol = item.get("symbol")?.as_str()?;
            let quantity = required_decimal_field(item, "positionAmt").ok()?;
            if quantity.mantissa == 0 {
                return None;
            }
            Some(Ok(Position {
                instrument_id: format!("instrument:binance:{symbol}"),
                market_id: Some(format!("market:binance:{symbol}")),
                quantity,
                average_price: required_decimal_field(item, "entryPrice").ok(),
                mark_price: required_decimal_field(item, "markPrice").ok(),
                unrealized_pnl: required_decimal_field(item, "unRealizedProfit").ok(),
                ..Default::default()
            }))
        })
        .collect::<Result<Vec<_>, String>>()?;

    let open_orders = orders
        .as_array()
        .ok_or_else(|| "Binance futures open orders is not an array".to_string())?
        .iter()
        .map(normalize_open_order)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances: balances.clone(),
        collateral: balances,
        positions,
        open_orders,
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: Some(AccountModel::Contract),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn normalize_open_order(value: &Value) -> Result<OpenOrder, String> {
    let venue_order_id = value
        .get("orderId")
        .map(value_as_string)
        .ok_or_else(|| "Binance futures open order id is missing".to_string())?;
    let symbol = value
        .get("symbol")
        .and_then(Value::as_str)
        .ok_or_else(|| "Binance futures open order symbol is missing".to_string())?;
    Ok(OpenOrder {
        order_id: value
            .get("clientOrderId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or(&venue_order_id)
            .into(),
        venue_order_id: Some(venue_order_id),
        instrument_id: format!("instrument:binance:{symbol}"),
        side: value
            .get("side")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
        quantity: required_decimal_field(value, "origQty")?,
        filled_quantity: required_decimal_field(value, "executedQty")?,
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

fn value_as_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

fn required_decimal_field(value: &Value, field: &str) -> Result<DecimalValue, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("Binance futures field is missing: {field}"))
        .and_then(decimal)
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    let value = value.trim();
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let mut parts = unsigned.split('.');
    let whole = parts.next().unwrap_or("0");
    let fraction = parts.next().unwrap_or("");
    if parts.next().is_some()
        || whole.is_empty()
        || !whole.chars().all(|c| c.is_ascii_digit())
        || !fraction.chars().all(|c| c.is_ascii_digit())
        || fraction.len() > 18
    {
        return Err(format!("invalid decimal: {value}"));
    }
    let mut mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| format!("decimal overflow: {value}"))?;
    if negative {
        mantissa = -mantissa;
    }
    Ok(DecimalValue::new(mantissa, fraction.len() as u8))
}

fn product_name(product: ProductFamily) -> &'static str {
    match product {
        ProductFamily::UsdMFutures => "usd-m-futures",
        ProductFamily::CoinMFutures => "coin-m-futures",
        _ => "futures",
    }
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::normalize_account;
    use crate::domain::account::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };

    #[test]
    fn normalizes_futures_assets_and_non_zero_positions() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: "usd_m_futures".into(),
            environment: "live".into(),
            account_model: Some("contract".into()),
        };
        let account = serde_json::json!({"assets":[{"asset":"USDT","walletBalance":"100.5","availableBalance":"90.25"}]});
        let positions = serde_json::json!([{"symbol":"BTCUSDT","positionAmt":"0.25","entryPrice":"60000","markPrice":"61000","unRealizedProfit":"250"},{"symbol":"ETHUSDT","positionAmt":"0"}]);
        let result = normalize_account(
            &segment,
            &account,
            &positions,
            &serde_json::json!([{"orderId":8,"clientOrderId":"future-8","symbol":"BTCUSDT","side":"SELL","origQty":"2","executedQty":"0","status":"NEW"}]),
        )
        .unwrap();
        assert_eq!(result.balances[0].asset_code, "USDT");
        assert_eq!(result.positions.len(), 1);
        assert_eq!(result.positions[0].quantity.mantissa, 25);
        assert_eq!(result.open_orders[0].order_id, "future-8");
    }
}

type FuturesSocket = WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>;

/// Binance Futures user-data stream kept beside the REST account gateway so
/// both capabilities share the same signed listen-key client.
pub struct BinanceFuturesAccountStreamConnection {
    connection: ManagedConnection,
    client: BinanceFuturesAccountClient,
    endpoint: String,
    socket: Option<FuturesSocket>,
    product: ProductFamily,
    segment_key: String,
}

impl BinanceFuturesAccountStreamConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
        ) {
            return Err("Binance futures account stream requires USD-M or Coin-M".into());
        }
        let client = BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let endpoint = websocket_endpoint.into().trim_end_matches('/').to_string();
        if !(endpoint.starts_with("wss://") || endpoint.starts_with("ws://")) {
            return Err(
                "Binance futures account stream endpoint must start with ws:// or wss://".into(),
            );
        }
        let segment_key = segment_key.into();
        if segment_key.trim().is_empty() {
            return Err("Binance futures account stream segment key is required".into());
        }
        let spec = ConnectionSpec {
            connection_id: format!("account.binance.{}.private-stream", product_name(product)),
            provider: "binance".into(),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::UserStream,
            capability: IntegrationCapability::AccountStream,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
            endpoint,
            socket: None,
            product,
            segment_key,
        })
    }

    fn open(&mut self) -> Result<(), String> {
        let listen_key = self
            .client
            .listen_key()
            .map_err(|error| error.to_string())?;
        let (socket, _) = connect(format!("{}/ws/{listen_key}", self.endpoint))
            .map_err(|error| error.to_string())?;
        self.socket = Some(socket);
        Ok(())
    }
}

impl Connection for BinanceFuturesAccountStreamConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        if self.connection.state().lifecycle == crate::domain::ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.connection.start()?;
        if let Err(error) = self.open() {
            let _ = self.connection.stop();
            return Err(error);
        }
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> {
        if let Some(mut socket) = self.socket.take() {
            let _ = socket.close(None);
        }
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        let _ = self.stop();
        self.connection.reconnect()?;
        self.open()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl AccountEventStreamConnection for BinanceFuturesAccountStreamConnection {
    fn next_account_event(&mut self) -> Result<Option<AccountEvent>, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let message = self
            .socket
            .as_mut()
            .ok_or(IntegrationError::NotReady)?
            .read()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let Message::Text(text) = message else {
            return Ok(None);
        };
        parse_user_event(&self.segment_key, self.product, &text)
            .map_err(IntegrationError::InvalidPayload)
    }
}

fn parse_user_event(
    segment_key: &str,
    product: ProductFamily,
    text: &str,
) -> Result<Option<AccountEvent>, String> {
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    match value.get("e").and_then(Value::as_str).unwrap_or_default() {
        "ORDER_TRADE_UPDATE" => {
            let row = value
                .get("o")
                .ok_or_else(|| "Binance futures order event is missing".to_string())?;
            let order_id = row
                .get("c")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance futures client order id is missing".to_string())?;
            let status = match row.get("X").and_then(Value::as_str).unwrap_or_default() {
                "NEW" => OrderStatus::Acknowledged,
                "PARTIALLY_FILLED" => OrderStatus::PartiallyFilled,
                "FILLED" => OrderStatus::Filled,
                "CANCELED" => OrderStatus::Canceled,
                "REJECTED" => OrderStatus::Rejected,
                "EXPIRED" | "EXPIRED_IN_MATCH" => OrderStatus::Expired,
                _ => OrderStatus::Unknown,
            };
            let occurred_at_unix_nanos =
                value.get("E").and_then(Value::as_u64).unwrap_or_default() * 1_000_000;
            let mut events = vec![AccountEvent::Order(OrderEvent {
                order_id: order_id.to_owned(),
                status,
                venue_order_id: row.get("i").map(value_string),
                filled_quantity: row
                    .get("z")
                    .and_then(Value::as_str)
                    .map(decimal)
                    .transpose()?,
                occurred_at_unix_nanos,
                reason: row
                    .get("r")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .into(),
            })];
            if let Some(quantity) = row
                .get("l")
                .and_then(Value::as_str)
                .filter(|value| *value != "0" && !value.is_empty())
            {
                let symbol = row
                    .get("s")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "Binance futures fill symbol is missing".to_string())?;
                let price = row
                    .get("L")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "Binance futures fill price is missing".to_string())?;
                events.push(AccountEvent::Fill(FillEvent {
                    fill_id: row
                        .get("t")
                        .map(value_string)
                        .filter(|value| value != "-1")
                        .unwrap_or_else(|| format!("{order_id}:{occurred_at_unix_nanos}")),
                    order_id: order_id.into(),
                    segment_key: segment_key.into(),
                    instrument_id: format!("instrument:{}", symbol.to_ascii_lowercase()),
                    side: row.get("S").and_then(Value::as_str).unwrap_or("BUY").into(),
                    quantity: decimal(quantity)?,
                    price: decimal(price)?,
                    fee_asset: row.get("N").and_then(Value::as_str).map(str::to_owned),
                    fee_amount: row
                        .get("n")
                        .and_then(Value::as_str)
                        .filter(|value| *value != "0" && !value.is_empty())
                        .map(decimal)
                        .transpose()?,
                    occurred_at_unix_nanos,
                }));
            }
            Ok(Some(AccountEvent::Batch(events)))
        }
        "ACCOUNT_UPDATE" => {
            let data = value
                .get("a")
                .ok_or_else(|| "Binance futures account event is missing".to_string())?;
            let balances: Vec<Balance> = data
                .get("B")
                .and_then(Value::as_array)
                .map_or(&[][..], Vec::as_slice)
                .iter()
                .filter_map(|row| {
                    let code = row.get("a").and_then(Value::as_str)?;
                    let total =
                        decimal(row.get("wb").and_then(Value::as_str).unwrap_or("0")).ok()?;
                    Some(Balance {
                        asset_id: format!("asset:crypto:{code}"),
                        asset_code: code.into(),
                        total,
                        available: stream_decimal_field(row, "cw"),
                        ..Default::default()
                    })
                })
                .collect();
            let positions = data
                .get("P")
                .and_then(Value::as_array)
                .map_or(&[][..], Vec::as_slice)
                .iter()
                .filter_map(|row| {
                    let symbol = row.get("s").and_then(Value::as_str)?;
                    let quantity =
                        decimal(row.get("pa").and_then(Value::as_str).unwrap_or("0")).ok()?;
                    let side = row.get("ps").and_then(Value::as_str).unwrap_or("BOTH");
                    Some(Position {
                        instrument_id: format!(
                            "instrument:binance:{}:{symbol}:{side}",
                            product_name(product)
                        ),
                        market_id: Some(format!(
                            "market:binance:{}:{symbol}",
                            product_name(product)
                        )),
                        quantity,
                        average_price: stream_decimal_field(row, "ep"),
                        unrealized_pnl: stream_decimal_field(row, "up"),
                        ..Default::default()
                    })
                })
                .collect();
            Ok(Some(AccountEvent::Snapshot(AccountSnapshot {
                segment_key: segment_key.into(),
                balances: balances.clone(),
                collateral: balances,
                positions,
                open_orders: Vec::new(),
                status: AccountStatus::Ready,
                observed_at_unix_nanos: value.get("E").and_then(Value::as_u64).unwrap_or_default()
                    * 1_000_000,
                equity: None,
                initial_equity: None,
                net_profit: None,
                account_model: None,
                margin_mode: None,
                position_mode: None,
                partial: true,
            })))
        }
        _ => Ok(None),
    }
}

fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
fn stream_decimal_field(value: &Value, field: &str) -> Option<DecimalValue> {
    value
        .get(field)
        .and_then(Value::as_str)
        .and_then(|value| decimal(value).ok())
}
