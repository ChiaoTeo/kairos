//! Binance Spot private account REST adapter.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::domain::account::{
    ExternalAccountModel as AccountModel, ExternalAccountSegment as AccountSegment,
    ExternalAccountSnapshot as AccountSnapshot, ExternalAccountStatus as AccountStatus,
    ExternalBalance as Balance, ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder,
};
use serde_json::Value;

use crate::application::{
    AccountCredentialInspectionConnection, AccountMarketProfileConnection, AccountReadConnection,
    Connection, ConnectionSpec, ExternalAccountCredentialProfile,
    ExternalMarketProfile as AccountMarketProfile,
    ExternalMarketProfileRequest as AccountMarketProfileRequest, IntegrationError,
};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::auth::signed_query;
use crate::services::connections::ManagedConnection;
use crate::services::drivers::http::{ExchangeError, PublicHttpClient};

pub struct BinanceSpotAccountConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
}

pub struct BinanceSpotAccountMarketProfileConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
}

impl BinanceSpotAccountMarketProfileConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "account.binance.spot.market-profile.rest".into(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountMarketProfileRead,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }
}

impl Connection for BinanceSpotAccountMarketProfileConnection {
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

impl AccountMarketProfileConnection for BinanceSpotAccountMarketProfileConnection {
    fn fetch_market_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, IntegrationError> {
        if request.source_symbol.trim().is_empty() {
            return Err(IntegrationError::InvalidPayload(
                "Binance market profile symbol is required".into(),
            ));
        }
        self.start().map_err(IntegrationError::Transport)?;
        let fee_payload = self
            .client
            .trade_fee(&request.source_symbol)
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let account_payload = self
            .client
            .account()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let burn_payload = self
            .client
            .bnb_burn_status()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_market_profile(request, &fee_payload, &account_payload, &burn_payload)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl BinanceSpotAccountConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "account.binance.spot.rest".into(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
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

impl Connection for BinanceSpotAccountConnection {
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

impl AccountReadConnection for BinanceSpotAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let payload = self
            .client
            .account()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let orders = self
            .client
            .open_orders()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_account(segment, &payload, &orders).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for BinanceSpotAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        self.start()?;
        let payload = self.client.account().map_err(|error| error.to_string())?;
        let mut permissions: Vec<String> = payload
            .get("permissions")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
        if !permissions.iter().any(|value| value == "read") {
            permissions.push("read".into());
        }
        if payload
            .get("canTrade")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            permissions.push("trade".into());
        }
        let account_type = payload
            .get("accountType")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let mut attributes = std::collections::BTreeMap::new();
        if let Some(value) = account_type.clone() {
            attributes.insert("account_type".into(), value);
        }
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type,
            permissions,
            segments: vec!["spot".into()],
            attributes,
        })
    }
}

pub(crate) struct BinanceSpotAccountClient {
    http: PublicHttpClient,
    api_key: String,
    secret: String,
    base_url: String,
}

impl BinanceSpotAccountClient {
    pub(crate) fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if api_key.trim().is_empty() || secret.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance Spot account credentials are required".into(),
            ));
        }
        if base_url.is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance Spot account base URL is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/binance-spot-account")?,
            api_key,
            secret,
            base_url,
        })
    }

    pub(crate) fn account(&self) -> Result<Value, ExchangeError> {
        let mut params = BTreeMap::new();
        params.insert("timestamp".into(), now_millis().to_string());
        params.insert("recvWindow".into(), "10000".into());
        let signed = signed_query(&self.secret, params.into_iter())?;
        let endpoint = format!("{}/api/v3/account", self.base_url);
        let mut query = url::form_urlencoded::parse(signed.query.as_bytes())
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect::<Vec<_>>();
        query.push(("signature".into(), signed.signature));
        let refs = query
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect::<Vec<_>>();
        self.http.get_json_with_headers_and_query(
            &endpoint,
            &refs,
            &[("X-MBX-APIKEY", self.api_key.clone())],
        )
    }

    fn open_orders(&self) -> Result<Value, ExchangeError> {
        self.signed_request("/api/v3/openOrders", BTreeMap::new(), RequestMethod::Get)
    }

    pub(crate) fn query_open_orders(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request("/api/v3/openOrders", params, RequestMethod::Get)
    }
    pub(crate) fn query_history(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request("/api/v3/allOrders", params, RequestMethod::Get)
    }
    pub(crate) fn query_detail(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request("/api/v3/order", params, RequestMethod::Get)
    }

    fn trade_fee(&self, symbol: &str) -> Result<Value, ExchangeError> {
        let mut params = BTreeMap::new();
        params.insert("symbol".into(), symbol.to_ascii_uppercase());
        self.signed_request("/sapi/v1/asset/tradeFee", params, RequestMethod::Get)
    }

    fn bnb_burn_status(&self) -> Result<Value, ExchangeError> {
        self.signed_request("/sapi/v1/bnbBurn", BTreeMap::new(), RequestMethod::Get)
    }

    pub(super) fn submit_order(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request("/api/v3/order", params, RequestMethod::Post)
    }

    pub(super) fn cancel_order(
        &self,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request("/api/v3/order", params, RequestMethod::Delete)
    }

    pub(super) fn listen_key(&self) -> Result<String, ExchangeError> {
        let endpoint = format!("{}/api/v3/userDataStream", self.base_url);
        let payload = self.http.post_json_with_headers(
            &endpoint,
            &[("X-MBX-APIKEY", self.api_key.clone())],
            &Value::Object(Default::default()),
        )?;
        payload
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| ExchangeError::InvalidRequest("Binance listen key is missing".into()))
    }

    pub(crate) fn signed_post(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(path, params, RequestMethod::Post)
    }

    pub(crate) fn signed_delete(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(path, params, RequestMethod::Delete)
    }

    pub(crate) fn margin_listen_key(
        &self,
        isolated_symbol: Option<&str>,
    ) -> Result<String, ExchangeError> {
        let endpoint = if let Some(symbol) = isolated_symbol {
            let query = url::form_urlencoded::Serializer::new(String::new())
                .append_pair("symbol", &symbol.to_ascii_uppercase())
                .finish();
            format!("{}/sapi/v1/userDataStream/isolated?{query}", self.base_url)
        } else {
            format!("{}/sapi/v1/userDataStream", self.base_url)
        };
        let payload = self.http.post_json_with_headers(
            &endpoint,
            &[("X-MBX-APIKEY", self.api_key.clone())],
            &Value::Object(Default::default()),
        )?;
        payload
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| {
                ExchangeError::InvalidRequest("Binance margin listen key is missing".into())
            })
    }

    pub(crate) fn signed_get(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
    ) -> Result<Value, ExchangeError> {
        self.signed_request(path, params, RequestMethod::Get)
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
}

pub(super) enum RequestMethod {
    Get,
    Post,
    Delete,
}

fn normalize_account(
    segment: &AccountSegment,
    payload: &Value,
    orders: &Value,
) -> Result<AccountSnapshot, String> {
    let balances = payload
        .get("balances")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance account balances is missing".to_string())?;
    let mut result = Vec::new();
    for item in balances {
        let code = item
            .get("asset")
            .and_then(Value::as_str)
            .ok_or_else(|| "Binance account balance asset is missing".to_string())?;
        let free = decimal(
            item.get("free")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance account balance free is missing".to_string())?,
        )?;
        let locked = decimal(
            item.get("locked")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance account balance locked is missing".to_string())?,
        )?;
        let scale = free.scale.max(locked.scale);
        let total = DecimalValue::new(
            rescale(free, scale)?
                .checked_add(rescale(locked, scale)?)
                .ok_or_else(|| "balance quantity overflow".to_string())?,
            scale,
        );
        result.push(Balance {
            asset_id: format!("asset:crypto:{code}"),
            asset_code: code.into(),
            total,
            available: Some(free),
            locked: Some(locked),
            ..Default::default()
        });
    }
    let open_orders = orders
        .as_array()
        .ok_or_else(|| "Binance open orders is not an array".to_string())?
        .iter()
        .map(normalize_open_order)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(AccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances: result,
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders,
        status: AccountStatus::Ready,
        observed_at_unix_nanos: now_nanos(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: segment
            .account_model
            .as_deref()
            .and_then(AccountModel::parse),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn normalize_market_profile(
    request: &AccountMarketProfileRequest,
    fee_payload: &Value,
    account_payload: &Value,
    burn_payload: &Value,
) -> Result<AccountMarketProfile, String> {
    let row = fee_payload
        .as_array()
        .and_then(|rows| rows.first())
        .ok_or_else(|| "Binance trade fee response is missing".to_string())?;
    let maker_fee = decimal_field(row, "maker")
        .or_else(|| decimal_field(row, "makerCommission"))
        .ok_or_else(|| "Binance maker fee is missing".to_string())?;
    let taker_fee = decimal_field(row, "taker")
        .or_else(|| decimal_field(row, "takerCommission"))
        .ok_or_else(|| "Binance taker fee is missing".to_string())?;
    let burn_enabled = burn_payload
        .get("spotBNBBurn")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let account_model = account_payload
        .get("accountType")
        .and_then(Value::as_str)
        .and_then(AccountModel::parse);
    Ok(AccountMarketProfile {
        account_id: request.account_id.clone(),
        segment_key: request.segment_key.clone(),
        market_id: request.market_id.clone(),
        account_model,
        margin_mode: None,
        position_mode: None,
        maker_fee: Some(maker_fee),
        taker_fee: Some(taker_fee),
        fee_currency: Some("BNB".into()),
        fee_discount: burn_enabled.then_some(DecimalValue::new(25, 2)),
        fee_tier: burn_enabled.then(|| "bnb_burn".into()),
        source: "binance.spot".into(),
        observed_at_unix_nanos: now_nanos(),
    })
}

fn decimal_field(value: &Value, field: &str) -> Option<DecimalValue> {
    value
        .get(field)
        .and_then(Value::as_str)
        .and_then(|value| decimal(value).ok())
}

fn normalize_open_order(value: &Value) -> Result<OpenOrder, String> {
    let venue_order_id = value
        .get("orderId")
        .map(value_as_string)
        .ok_or_else(|| "Binance open order id is missing".to_string())?;
    let symbol = value
        .get("symbol")
        .and_then(Value::as_str)
        .ok_or_else(|| "Binance open order symbol is missing".to_string())?;
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
        quantity: decimal(value.get("origQty").and_then(Value::as_str).unwrap_or("0"))?,
        filled_quantity: decimal(
            value
                .get("executedQty")
                .and_then(Value::as_str)
                .unwrap_or("0"),
        )?,
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

fn rescale(value: DecimalValue, scale: u8) -> Result<i64, String> {
    let mut mantissa = value.mantissa;
    for _ in value.scale..scale {
        mantissa = mantissa
            .checked_mul(10)
            .ok_or_else(|| "decimal rescale overflow".to_string())?;
    }
    Ok(mantissa)
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
    let scale = fraction.len() as u8;
    let digits = format!("{whole}{fraction}");
    let mut mantissa = digits
        .parse::<i64>()
        .map_err(|_| format!("decimal overflow: {value}"))?;
    if negative {
        mantissa = -mantissa;
    }
    Ok(DecimalValue::new(mantissa, scale))
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
    use super::{normalize_account, normalize_market_profile};
    use crate::application::ExternalMarketProfileRequest as AccountMarketProfileRequest;
    use crate::domain::account::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };

    #[test]
    fn normalizes_private_balances_without_vendor_payloads() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: "spot".into(),
            environment: "live".into(),
            account_model: None,
        };
        let value =
            serde_json::json!({"balances":[{"asset":"USDT","free":"10.25","locked":"0.75"}]});
        let result = normalize_account(
            &segment,
            &value,
            &serde_json::json!([{"orderId":7,"clientOrderId":"local-7","symbol":"BTCUSDT","side":"BUY","origQty":"1.5","executedQty":"0.5","status":"NEW"}]),
        )
        .unwrap();
        assert_eq!(result.balances[0].asset_id, "asset:crypto:USDT");
        assert_eq!(result.balances[0].total.mantissa, 1100);
        assert_eq!(result.balances[0].total.scale, 2);
        assert_eq!(result.open_orders[0].order_id, "local-7");
        assert_eq!(result.open_orders[0].filled_quantity.mantissa, 5);
    }

    #[test]
    fn normalizes_spot_market_fee_and_bnb_discount_profile() {
        let request = AccountMarketProfileRequest {
            account_id: "main".into(),
            segment_key: "spot".into(),
            market_id: "market:binance:BTCUSDT".into(),
            source_symbol: "BTCUSDT".into(),
        };
        let result = normalize_market_profile(
            &request,
            &serde_json::json!([{"symbol":"BTCUSDT","maker":"0.001","taker":"0.0012"}]),
            &serde_json::json!({"accountType":"SPOT"}),
            &serde_json::json!({"spotBNBBurn":true}),
        )
        .unwrap();
        assert_eq!(
            result.account_model,
            Some(crate::domain::account::ExternalAccountModel::NoMargin)
        );
        assert_eq!(result.fee_discount.unwrap().mantissa, 25);
        assert_eq!(result.fee_tier.as_deref(), Some("bnb_burn"));
    }
}
