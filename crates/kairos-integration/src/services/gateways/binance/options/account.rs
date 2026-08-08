use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::domain::account::{
    ExternalAccountModel as AccountModel, ExternalAccountSegment as AccountSegment,
    ExternalAccountSnapshot as AccountSnapshot, ExternalAccountStatus as AccountStatus,
    ExternalBalance as Balance, ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder,
    ExternalPosition as Position,
};
use serde_json::Value;

use crate::application::{
    AccountCredentialInspectionConnection, AccountReadConnection, Connection, ConnectionSpec,
    ExternalAccountCredentialProfile, IntegrationError,
};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::auth::signed_query;
use crate::services::connections::ManagedConnection;
use crate::services::drivers::http::{ExchangeError, PublicHttpClient};

pub(crate) struct BinanceOptionsAccountClient {
    http: PublicHttpClient,
    api_key: String,
    secret: String,
    base_url: String,
}

impl BinanceOptionsAccountClient {
    pub(crate) fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if api_key.trim().is_empty() || secret.trim().is_empty() || base_url.is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance Options credentials and base URL are required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/binance-options-account")?,
            api_key,
            secret,
            base_url,
        })
    }

    pub(crate) fn request(
        &self,
        path: &str,
        mut params: BTreeMap<String, String>,
        method: Method,
    ) -> Result<Value, ExchangeError> {
        params.insert("timestamp".into(), now_millis().to_string());
        params.insert("recvWindow".into(), "10000".into());
        let signed = signed_query(&self.secret, params.into_iter())?;
        let endpoint = format!("{}{}", self.base_url, path);
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
            Method::Get => self
                .http
                .get_json_with_headers_and_query(&endpoint, &refs, &headers),
            Method::Post => self
                .http
                .post_json_with_headers_and_query(&endpoint, &refs, &headers),
            Method::Delete => self
                .http
                .delete_json_with_headers_and_query(&endpoint, &refs, &headers),
        }
    }
}

pub(crate) enum Method {
    Get,
    Post,
    Delete,
}

pub struct BinanceOptionsAccountConnection {
    connection: ManagedConnection,
    client: BinanceOptionsAccountClient,
}

impl BinanceOptionsAccountConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceOptionsAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "account.binance.options.rest".into(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Options),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountRead,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }
}

impl Connection for BinanceOptionsAccountConnection {
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

impl AccountReadConnection for BinanceOptionsAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let payload = self
            .client
            .request("/eapi/v1/account", BTreeMap::new(), Method::Get)
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let orders = self
            .client
            .request("/eapi/v1/openOrders", BTreeMap::new(), Method::Get)
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_account(segment, &payload, &orders).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for BinanceOptionsAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        self.start()?;
        let payload = self
            .client
            .request("/eapi/v1/account", BTreeMap::new(), Method::Get)
            .map_err(|error| error.to_string())?;
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type: payload
                .get("accountType")
                .and_then(Value::as_str)
                .map(str::to_owned),
            permissions: vec!["read".into()],
            segments: vec!["options".into()],
            attributes: Default::default(),
        })
    }
}

fn normalize_account(
    segment: &AccountSegment,
    payload: &Value,
    orders: &Value,
) -> Result<AccountSnapshot, String> {
    let assets = payload
        .get("assets")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance Options assets are missing".to_string())?;
    let balances: Vec<Balance> = assets
        .iter()
        .filter_map(|row| {
            let code = row.get("asset").and_then(Value::as_str)?;
            let total = decimal(
                row.get("marginBalance")
                    .or_else(|| row.get("available"))
                    .and_then(Value::as_str)
                    .unwrap_or("0"),
            )
            .ok()?;
            Some(Balance {
                asset_id: format!("asset:crypto:{code}"),
                asset_code: code.into(),
                total,
                available: decimal_field(row, "available"),
                locked: decimal_field(row, "locked").or_else(|| decimal_field(row, "freeze")),
                ..Default::default()
            })
        })
        .collect();
    let positions = payload
        .get("positions")
        .and_then(Value::as_array)
        .map_or(&[][..], |rows| rows.as_slice())
        .iter()
        .filter_map(|row| {
            let symbol = row.get("symbol").and_then(Value::as_str)?;
            let quantity = decimal_field(row, "quantity")?;
            if quantity.mantissa == 0 {
                return None;
            }
            Some(Position {
                instrument_id: format!("instrument:binance:{symbol}"),
                market_id: Some(format!("market:binance:{symbol}")),
                quantity,
                average_price: decimal_field(row, "averagePrice"),
                ..Default::default()
            })
        })
        .collect();
    let open_orders = orders
        .as_array()
        .ok_or_else(|| "Binance Options open orders is not an array".to_string())?
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
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string())
        })
        .ok_or_else(|| "Binance Options open order id is missing".to_string())?;
    let symbol = value
        .get("symbol")
        .and_then(Value::as_str)
        .ok_or_else(|| "Binance Options open order symbol is missing".to_string())?;
    Ok(OpenOrder {
        order_id: value
            .get("clientOrderId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or(&venue_order_id)
            .into(),
        venue_order_id: Some(venue_order_id),
        instrument_id: format!("instrument:binance:options:{symbol}"),
        side: value
            .get("side")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
        quantity: decimal_field(value, "quantity").unwrap_or_default(),
        filled_quantity: decimal_field(value, "executedQty").unwrap_or_default(),
        status: value
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

fn decimal_field(row: &Value, field: &str) -> Option<DecimalValue> {
    row.get(field)
        .and_then(Value::as_str)
        .and_then(|value| decimal(value).ok())
}
fn decimal(value: &str) -> Result<DecimalValue, String> {
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let mut parts = unsigned.split('.');
    let whole = parts.next().unwrap_or("0");
    let fraction = parts.next().unwrap_or("");
    if parts.next().is_some()
        || !whole.chars().all(|c| c.is_ascii_digit())
        || !fraction.chars().all(|c| c.is_ascii_digit())
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
    fn normalizes_options_assets_and_positions() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: "options".into(),
            environment: "live".into(),
            account_model: Some("contract".into()),
        };
        let snapshot = normalize_account(&segment, &serde_json::json!({"assets":[{"asset":"USDT","marginBalance":"1000","available":"900"}],"positions":[{"symbol":"BTC-250101-60000-C","quantity":"1","averagePrice":"100"}]}), &serde_json::json!([])).unwrap();
        assert_eq!(snapshot.positions.len(), 1);
        assert_eq!(snapshot.balances[0].asset_code, "USDT");
    }
}
