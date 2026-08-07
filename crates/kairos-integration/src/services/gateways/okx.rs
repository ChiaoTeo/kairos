//! OKX private account REST adapter.
//!
//! OKX's account API is intentionally normalized here.  Account never sees
//! the `code/data/details` response envelope or the OK-ACCESS credentials.

use std::time::{SystemTime, UNIX_EPOCH};

use crate::domain::account::{
    ExternalAccountModel as AccountModel, ExternalAccountSegment as AccountSegment,
    ExternalAccountSnapshot as AccountSnapshot, ExternalAccountStatus as AccountStatus,
    ExternalBalance as Balance, ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder,
    ExternalPosition as Position,
};
use chrono::{SecondsFormat, Utc};
use serde_json::Value;

use crate::application::{
    AccountCredentialInspectionConnection, AccountMarketProfileConnection, AccountReadConnection,
    Connection, ConnectionSpec, ExternalAccountCredentialProfile,
    ExternalMarketProfile as AccountMarketProfile,
    ExternalMarketProfileRequest as AccountMarketProfileRequest, ExternalOrder, ExternalOrderQuery,
    IntegrationError, OrderEntryConnection, OrderQueryConnection,
};
use crate::domain::{
    AccessScope, IntegrationCapability, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus,
    OrderSide, OrderType, ProductFamily, TimeInForce, TransportKind,
};
use crate::services::auth::okx_signature;
use crate::services::connections::ManagedConnection;
use crate::services::drivers::http::{ExchangeError, PublicHttpClient};

pub struct OkxAccountConnection {
    connection: ManagedConnection,
    client: OkxAccountClient,
}

pub struct OkxAccountMarketProfileConnection {
    connection: ManagedConnection,
    client: OkxAccountClient,
}

impl OkxAccountMarketProfileConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = OkxAccountClient::new(product, api_key, secret, passphrase, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: format!("account.okx.{}.market-profile.rest", product_name(product)),
            provider: "okx".into(),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountMarketProfileRead,
            credential_id: Some("okx".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }
}

impl Connection for OkxAccountMarketProfileConnection {
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

impl AccountMarketProfileConnection for OkxAccountMarketProfileConnection {
    fn fetch_market_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, IntegrationError> {
        if request.source_symbol.trim().is_empty() {
            return Err(IntegrationError::InvalidPayload(
                "OKX market profile instrument is required".into(),
            ));
        }
        self.start().map_err(IntegrationError::Transport)?;
        let fee = self
            .client
            .trade_fee(&request.source_symbol)
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let config = self
            .client
            .config()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_market_profile(request, &fee, &config).map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxAccountConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::CrossMargin
                | ProductFamily::IsolatedMargin
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err("OKX account requires spot, futures, or options product".into());
        }
        let client = OkxAccountClient::new(product, api_key, secret, passphrase, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: format!("account.okx.{}.rest", product_name(product)),
            provider: "okx".into(),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountRead,
            credential_id: Some("okx".into()),
            asset_type: None,
        };
        let connection = ManagedConnection::new(spec, Vec::new())?;
        Ok(Self { connection, client })
    }
}

impl Connection for OkxAccountConnection {
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

impl AccountReadConnection for OkxAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let balance = self
            .client
            .balance()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let positions = self
            .client
            .positions()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let orders = self
            .client
            .pending_orders()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_account(segment, &balance, &positions, &orders)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountCredentialInspectionConnection for OkxAccountConnection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String> {
        self.start()?;
        let payload = self.client.config().map_err(|error| error.to_string())?;
        let row = payload
            .get("data")
            .and_then(Value::as_array)
            .and_then(|rows| rows.first())
            .ok_or_else(|| "OKX account config data is missing".to_string())?;
        let account_type = row
            .get("acctLv")
            .and_then(Value::as_str)
            .map(|value| value.to_string());
        let remote_identity = row.get("uid").and_then(Value::as_str).map(str::to_owned);
        let mut attributes = std::collections::BTreeMap::new();
        if let Some(value) = row.get("posMode").and_then(Value::as_str) {
            attributes.insert("position_mode".into(), value.into());
        }
        Ok(ExternalAccountCredentialProfile {
            remote_identity,
            account_type,
            permissions: vec!["read".into()],
            segments: vec![product_name(self.client.product).into()],
            attributes,
        })
    }
}

pub(super) struct OkxAccountClient {
    http: PublicHttpClient,
    product: ProductFamily,
    api_key: String,
    secret: String,
    passphrase: String,
    base_url: String,
}

impl OkxAccountClient {
    pub(super) fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let passphrase = passphrase.into();
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if api_key.trim().is_empty() || secret.trim().is_empty() || passphrase.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "OKX api key, secret and passphrase are required".into(),
            ));
        }
        if base_url.is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "OKX base URL is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/okx-account")?,
            product,
            api_key,
            secret,
            passphrase,
            base_url,
        })
    }

    fn get(&self, path: &str, query: &[(&str, String)]) -> Result<Value, ExchangeError> {
        let query_string = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(query.iter().map(|(key, value)| (*key, value.as_str())))
            .finish();
        let request_path = if query_string.is_empty() {
            path.to_string()
        } else {
            format!("{path}?{query_string}")
        };
        let timestamp = Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true);
        let sign = okx_signature(&self.secret, &timestamp, "GET", &request_path, "")?;
        let headers = [
            ("OK-ACCESS-KEY", self.api_key.clone()),
            ("OK-ACCESS-SIGN", sign),
            ("OK-ACCESS-TIMESTAMP", timestamp),
            ("OK-ACCESS-PASSPHRASE", self.passphrase.clone()),
        ];
        let endpoint = format!("{}{}", self.base_url, path);
        self.http
            .get_json_with_headers_and_query(&endpoint, query, &headers)
            .and_then(|payload| check_okx_response(payload))
    }

    fn balance(&self) -> Result<Value, ExchangeError> {
        self.get("/api/v5/account/balance", &[])
    }

    fn positions(&self) -> Result<Value, ExchangeError> {
        let inst_type = match self.product {
            ProductFamily::Spot => "SPOT",
            ProductFamily::CrossMargin | ProductFamily::IsolatedMargin => "MARGIN",
            ProductFamily::UsdMFutures => "SWAP",
            ProductFamily::CoinMFutures => "FUTURES",
            ProductFamily::Options => "OPTION",
            _ => unreachable!(),
        };
        self.get(
            "/api/v5/account/positions",
            &[("instType", inst_type.into())],
        )
    }

    fn pending_orders(&self) -> Result<Value, ExchangeError> {
        self.get(
            "/api/v5/trade/orders-pending",
            &[("instType", instrument_type(self.product).into())],
        )
    }

    fn order_history(&self, query: &ExternalOrderQuery) -> Result<Value, ExchangeError> {
        self.get(
            "/api/v5/trade/orders-history",
            &query_params(self.product, query),
        )
    }

    fn order_detail(&self, query: &ExternalOrderQuery) -> Result<Value, ExchangeError> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| ExchangeError::InvalidRequest("order_id is required".into()))?;
        let mut params = query_params(self.product, query);
        params.push(("ordId", order_id.into()));
        self.get("/api/v5/trade/order", &params)
    }

    fn order_open(&self, query: &ExternalOrderQuery) -> Result<Value, ExchangeError> {
        let mut params = query_params(self.product, query);
        if let Some(symbol) = &query.symbol {
            params.push(("instId", symbol.to_ascii_uppercase()));
        }
        self.get("/api/v5/trade/orders-pending", &params)
    }

    fn trade_fee(&self, instrument: &str) -> Result<Value, ExchangeError> {
        self.get(
            "/api/v5/account/trade-fee",
            &[
                ("instType", instrument_type(self.product).into()),
                ("instId", instrument.into()),
            ],
        )
    }

    fn config(&self) -> Result<Value, ExchangeError> {
        self.get("/api/v5/account/config", &[])
    }

    pub(super) fn submit_order(&self, body: Value) -> Result<Value, ExchangeError> {
        self.post("/api/v5/trade/order", body)
    }

    pub(super) fn cancel_order(&self, body: Value) -> Result<Value, ExchangeError> {
        self.post("/api/v5/trade/cancel-order", body)
    }

    fn post(&self, path: &str, body: Value) -> Result<Value, ExchangeError> {
        let timestamp = Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true);
        let body_text = body.to_string();
        let sign = okx_signature(&self.secret, &timestamp, "POST", path, &body_text)?;
        let headers = [
            ("OK-ACCESS-KEY", self.api_key.clone()),
            ("OK-ACCESS-SIGN", sign),
            ("OK-ACCESS-TIMESTAMP", timestamp),
            ("OK-ACCESS-PASSPHRASE", self.passphrase.clone()),
            ("Content-Type", "application/json".into()),
        ];
        self.http
            .post_json_with_headers(&format!("{}{}", self.base_url, path), &headers, &body)
            .and_then(check_okx_response)
    }
}

pub struct OkxOrderConnection {
    connection: ManagedConnection,
    client: OkxAccountClient,
    product: ProductFamily,
}

pub struct OkxOrderQueryConnection {
    connection: ManagedConnection,
    client: OkxAccountClient,
}

impl OkxOrderQueryConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = OkxAccountClient::new(product, api_key, secret, passphrase, base_url)
            .map_err(|e| e.to_string())?;
        let connection = ManagedConnection::new(
            ConnectionSpec {
                connection_id: format!("execution.okx.{}.order-read.rest", product_name(product)),
                provider: "okx".into(),
                product: Some(product),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::OrderRead,
                credential_id: Some("okx".into()),
                asset_type: None,
            },
            Vec::new(),
        )?;
        Ok(Self { connection, client })
    }
}

impl Connection for OkxOrderQueryConnection {
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

impl OrderQueryConnection for OkxOrderQueryConnection {
    fn open_orders(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        self.start()?;
        normalize_okx_orders(&self.client.order_open(query).map_err(|e| e.to_string())?)
    }
    fn order_history(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        self.start()?;
        normalize_okx_orders(
            &self
                .client
                .order_history(query)
                .map_err(|e| e.to_string())?,
        )
    }
    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, String> {
        self.start()?;
        let value = self.client.order_detail(query).map_err(|e| e.to_string())?;
        let rows = value
            .get("data")
            .and_then(Value::as_array)
            .ok_or_else(|| "OKX order detail data is missing".to_string())?;
        rows.first().map(normalize_okx_order).transpose()
    }
}

fn query_params(product: ProductFamily, query: &ExternalOrderQuery) -> Vec<(&'static str, String)> {
    let mut values = vec![("instType", instrument_type(product).into())];
    if let Some(symbol) = &query.symbol {
        values.push(("instId", symbol.to_ascii_uppercase()));
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    values
}

fn normalize_okx_orders(value: &Value) -> Result<Vec<ExternalOrder>, String> {
    value
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX order query data is missing".to_string())?
        .iter()
        .map(normalize_okx_order)
        .collect()
}
fn normalize_okx_order(value: &Value) -> Result<ExternalOrder, String> {
    let text = |key: &str| {
        value
            .get(key)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .filter(|v| !v.is_empty())
    };
    let order_id = text("ordId").ok_or_else(|| "OKX order id is missing".to_string())?;
    let quantity = order_decimal(decimal_field(value, "sz")?);
    let filled_quantity = order_decimal(decimal_field(value, "accFillSz").unwrap_or_default());
    let average_fill_price = decimal_field(value, "avgPx").ok().map(order_decimal);
    Ok(ExternalOrder {
        order_id,
        client_order_id: text("clOrdId"),
        symbol: text("instId").unwrap_or_else(|| "UNKNOWN".into()),
        side: if text("side").as_deref() == Some("sell") {
            OrderSide::Sell
        } else {
            OrderSide::Buy
        },
        order_type: if text("ordType").as_deref() == Some("market") {
            OrderType::Market
        } else {
            OrderType::Limit
        },
        status: text("state").unwrap_or_else(|| "UNKNOWN".into()),
        quantity,
        filled_quantity,
        average_fill_price,
        occurred_at_unix_millis: value
            .get("uTime")
            .and_then(Value::as_str)
            .and_then(|v| v.parse().ok()),
    })
}

fn order_decimal(value: crate::domain::account::ExternalDecimal) -> crate::domain::DecimalValue {
    crate::domain::DecimalValue::new(value.mantissa, value.scale)
}

impl OkxOrderConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = OkxAccountClient::new(product, api_key, secret, passphrase, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: format!("execution.okx.{}.rest", product_name(product)),
            provider: "okx".into(),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderEntry,
            credential_id: Some("okx".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
            product,
        })
    }
}

impl Connection for OkxOrderConnection {
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

impl OrderEntryConnection for OkxOrderConnection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        let mut body = serde_json::Map::from_iter([
            ("instId".into(), Value::String(symbol(request)?)),
            (
                "tdMode".into(),
                Value::String(
                    match self.product {
                        ProductFamily::Spot => "cash",
                        ProductFamily::IsolatedMargin => "isolated",
                        _ => "cross",
                    }
                    .into(),
                ),
            ),
            ("side".into(), Value::String(side(request.side).into())),
            ("ordType".into(), Value::String(order_type(request).into())),
            (
                "sz".into(),
                Value::String(format_entry_decimal(request.quantity)),
            ),
            ("clOrdId".into(), Value::String(request.order_id.clone())),
        ]);
        if let Some(position_side) = &request.options.position_side {
            body.insert("posSide".into(), Value::String(position_side.clone()));
        }
        if let Some(reduce_only) = request.options.reduce_only {
            body.insert("reduceOnly".into(), Value::Bool(reduce_only));
        }
        if let Some(quote_asset) = &request.options.quote_asset {
            body.insert("tgtCcy".into(), Value::String(quote_asset.clone()));
        }
        if let (OrderType::Limit, Some(price)) = (request.order_type, request.limit_price) {
            body.insert("px".into(), Value::String(format_entry_decimal(price)));
        }
        let payload = self
            .client
            .submit_order(Value::Object(body))
            .map_err(|error| error.to_string())?;
        let row = payload
            .get("data")
            .and_then(Value::as_array)
            .and_then(|rows| rows.first())
            .ok_or_else(|| "OKX order response data is missing".to_string())?;
        let code = row.get("sCode").and_then(Value::as_str).unwrap_or("0");
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: if code == "0" {
                OrderEntryStatus::Accepted
            } else {
                OrderEntryStatus::Rejected
            },
            venue_order_id: row.get("ordId").and_then(Value::as_str).map(str::to_owned),
            filled_quantity: None,
            occurred_at_unix_nanos: now_nanos(),
            reason: row
                .get("sMsg")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .into(),
        })
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        self.start()?;
        let body = serde_json::json!({
            "instId": symbol(request)?,
            "ordId": venue_order_id,
        });
        let payload = self
            .client
            .cancel_order(body)
            .map_err(|error| error.to_string())?;
        let row = payload
            .get("data")
            .and_then(Value::as_array)
            .and_then(|rows| rows.first());
        let code = row
            .and_then(|value| value.get("sCode"))
            .and_then(Value::as_str)
            .unwrap_or("0");
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: if code == "0" {
                OrderEntryStatus::Canceled
            } else {
                OrderEntryStatus::Unknown
            },
            venue_order_id: Some(venue_order_id.into()),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos,
            reason: row
                .and_then(|value| value.get("sMsg"))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .into(),
        })
    }
}

fn check_okx_response(value: Value) -> Result<Value, ExchangeError> {
    if value.get("code").and_then(Value::as_str) != Some("0") {
        return Err(ExchangeError::Http {
            status: 200,
            body: value.to_string(),
        });
    }
    Ok(value)
}

fn normalize_market_profile(
    request: &AccountMarketProfileRequest,
    fee_payload: &Value,
    config_payload: &Value,
) -> Result<AccountMarketProfile, String> {
    let fee_row = fee_payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX trade fee data is missing".to_string())?;
    let config_row = config_payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX account config data is missing".to_string())?;
    let maker_fee =
        decimal_field(fee_row, "maker").or_else(|_| decimal_field(fee_row, "makerU"))?;
    let taker_fee =
        decimal_field(fee_row, "taker").or_else(|_| decimal_field(fee_row, "takerU"))?;
    let account_model = match config_row.get("acctLv").and_then(Value::as_str) {
        Some("1") => Some(AccountModel::NoMargin),
        Some("2") => Some(AccountModel::Margin),
        Some("3") => Some(AccountModel::Unified),
        Some("4") => Some(AccountModel::PortfolioMargin),
        _ => None,
    };
    Ok(AccountMarketProfile {
        account_id: request.account_id.clone(),
        segment_key: request.segment_key.clone(),
        market_id: request.market_id.clone(),
        account_model,
        margin_mode: None,
        position_mode: config_row
            .get("posMode")
            .and_then(Value::as_str)
            .map(str::to_owned),
        maker_fee: Some(maker_fee),
        taker_fee: Some(taker_fee),
        fee_currency: fee_row
            .get("feeCcy")
            .and_then(Value::as_str)
            .map(str::to_owned),
        fee_discount: None,
        fee_tier: fee_row
            .get("feeGroup")
            .and_then(Value::as_str)
            .map(str::to_owned),
        source: "okx".into(),
        observed_at_unix_nanos: now_nanos(),
    })
}

fn normalize_account(
    segment: &AccountSegment,
    balance: &Value,
    positions: &Value,
    orders: &Value,
) -> Result<AccountSnapshot, String> {
    let balance_row = balance
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX balance data is missing".to_string())?;
    let details = balance_row
        .get("details")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX balance details is missing".to_string())?;
    let balances = details
        .iter()
        .filter_map(|item| {
            let code = item.get("ccy")?.as_str()?;
            let total = decimal_field(item, "eq")
                .or_else(|_| decimal_field(item, "cashBal"))
                .ok()?;
            Some(Balance {
                asset_id: format!("asset:crypto:{code}"),
                asset_code: code.into(),
                total,
                available: decimal_field(item, "availBal").ok(),
                locked: decimal_field(item, "frozenBal").ok(),
                ..Default::default()
            })
        })
        .collect::<Vec<_>>();

    let rows = positions
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX positions data is missing".to_string())?;
    let positions = rows
        .iter()
        .filter_map(|item| {
            let symbol = item.get("instId")?.as_str()?;
            let mut quantity = decimal_field(item, "pos").ok()?;
            if item.get("posSide").and_then(Value::as_str) == Some("short") {
                quantity.mantissa = -quantity.mantissa.abs();
            }
            if quantity.mantissa == 0 {
                return None;
            }
            Some(Position {
                instrument_id: format!("instrument:okx:{symbol}"),
                market_id: Some(format!("market:okx:{symbol}")),
                quantity,
                average_price: decimal_field(item, "avgPx").ok(),
                mark_price: decimal_field(item, "markPx").ok(),
                unrealized_pnl: decimal_field(item, "upl").ok(),
                ..Default::default()
            })
        })
        .collect();

    let open_orders = orders
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX pending orders data is missing".to_string())?
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
        account_model: segment
            .account_model
            .as_deref()
            .and_then(AccountModel::parse),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn normalize_open_order(value: &Value) -> Result<OpenOrder, String> {
    let venue_order_id = value
        .get("ordId")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX pending order id is missing".to_string())?;
    let symbol = value
        .get("instId")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX pending order instrument is missing".to_string())?;
    Ok(OpenOrder {
        order_id: value
            .get("clOrdId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or(venue_order_id)
            .into(),
        venue_order_id: Some(venue_order_id.into()),
        instrument_id: format!("instrument:okx:{symbol}"),
        side: value
            .get("side")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
        quantity: decimal_field(value, "sz")?,
        filled_quantity: decimal_field(value, "accFillSz").unwrap_or_default(),
        status: value
            .get("state")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

fn decimal_field(value: &Value, field: &str) -> Result<DecimalValue, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("OKX field is missing: {field}"))
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
        ProductFamily::Spot => "spot",
        ProductFamily::CrossMargin => "cross-margin",
        ProductFamily::IsolatedMargin => "isolated-margin",
        ProductFamily::UsdMFutures => "swap",
        ProductFamily::CoinMFutures => "futures",
        ProductFamily::Options => "options",
        _ => "account",
    }
}

fn instrument_type(product: ProductFamily) -> &'static str {
    match product {
        ProductFamily::Spot => "SPOT",
        ProductFamily::CrossMargin | ProductFamily::IsolatedMargin => "MARGIN",
        ProductFamily::UsdMFutures => "SWAP",
        ProductFamily::CoinMFutures => "FUTURES",
        ProductFamily::Options => "OPTION",
        _ => "ANY",
    }
}

fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    let value = request
        .market_id
        .as_deref()
        .or_else(|| request.instrument_id.strip_prefix("instrument:okx:"))
        .or_else(|| request.instrument_id.strip_prefix("instrument:"))
        .ok_or_else(|| "OKX order requires a market_id or OKX instrument id".to_string())?;
    let value = value.rsplit(':').next().unwrap_or(value).trim();
    if value.is_empty() {
        return Err("OKX order instrument is empty".into());
    }
    Ok(value.replace('/', "-").to_ascii_uppercase())
}

fn side(value: OrderSide) -> &'static str {
    match value {
        OrderSide::Buy => "buy",
        OrderSide::Sell => "sell",
    }
}

fn order_type(request: &OrderEntryRequest) -> &'static str {
    match request.options.time_in_force {
        Some(TimeInForce::ImmediateOrCancel) => "ioc",
        Some(TimeInForce::FillOrKill) => "fok",
        _ if request.options.post_only == Some(true) => "post_only",
        _ => match request.order_type {
            OrderType::Market => "market",
            OrderType::Limit => "limit",
            OrderType::Stop => "market",
            OrderType::StopLimit => "limit",
        },
    }
}

fn format_entry_decimal(value: crate::domain::DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.abs().to_string();
    let scale = value.scale as usize;
    let padded = format!(
        "{}{}",
        "0".repeat(scale.saturating_sub(digits.len())),
        digits
    );
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
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
    fn normalizes_okx_balance_and_short_position() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("okx", "main").unwrap(),
            segment_key: "swap".into(),
            environment: "live".into(),
            account_model: Some("unified".into()),
        };
        let balance = serde_json::json!({"code":"0","data":[{"details":[{"ccy":"USDT","eq":"1000","availBal":"900","frozenBal":"100"}]}]});
        let positions = serde_json::json!({"code":"0","data":[{"instId":"BTC-USDT-SWAP","pos":"2","posSide":"short","avgPx":"60000","markPx":"59000","upl":"200"}]});
        let result = normalize_account(
            &segment,
            &balance,
            &positions,
            &serde_json::json!({"data":[{"ordId":"9","clOrdId":"okx-9","instId":"BTC-USDT-SWAP","side":"sell","sz":"1","accFillSz":"0","state":"live"}]}),
        )
        .unwrap();
        assert_eq!(result.balances[0].total.mantissa, 1000);
        assert_eq!(result.positions[0].quantity.mantissa, -2);
        assert_eq!(result.open_orders[0].order_id, "okx-9");
    }

    #[test]
    fn normalizes_okx_market_fee_and_account_mode_profile() {
        let request = AccountMarketProfileRequest {
            account_id: "main".into(),
            segment_key: "swap".into(),
            market_id: "market:okx:BTC-USDT-SWAP".into(),
            source_symbol: "BTC-USDT-SWAP".into(),
        };
        let result = normalize_market_profile(
            &request,
            &serde_json::json!({"code":"0","data":[{"maker":"-0.0002","taker":"0.0005","feeCcy":"USDT","feeGroup":"1"}]}),
            &serde_json::json!({"code":"0","data":[{"acctLv":"3","posMode":"long_short_mode"}]}),
        )
        .unwrap();
        assert_eq!(
            result.account_model,
            Some(crate::domain::account::ExternalAccountModel::Unified)
        );
        assert_eq!(result.position_mode.as_deref(), Some("long_short_mode"));
        assert_eq!(result.fee_currency.as_deref(), Some("USDT"));
    }
}
