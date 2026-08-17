//! OKX private account REST adapter.
//!
//! OKX's account API is intentionally normalized here.  Account never sees
//! the `code/data/details` response envelope or the OK-ACCESS credentials.

pub(crate) mod signing;
pub(crate) mod stream;

use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::account_facts::{
    external_instrument_ref, ExternalAccountModel as AccountModel,
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder, ExternalPosition as Position,
};
use chrono::{SecondsFormat, Utc};
use kairos_primitives::{ClientOrderId, OrderId, Symbol, UnixNanos};
use serde_json::Value;

use crate::application::capabilities::{
    OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType, TimeInForce,
};
use crate::application::{
    CommandOutcome, ExternalAccountCredentialProfile,
    ExternalMarketProfile as AccountMarketProfile,
    ExternalMarketProfileRequest as AccountMarketProfileRequest, ExternalOrder, ExternalOrderQuery,
    IndeterminateCommand, IntegrationError, ProviderRejection,
};
use crate::services::participants::okx::signing::okx_signature;
use crate::services::transport::http::{AsyncPublicHttpClient, ExchangeError, PublicHttpClient};

#[derive(Clone)]
pub(crate) struct OkxAccountClient {
    http: PublicHttpClient,
    async_http: AsyncPublicHttpClient,
    instrument_type: String,
    api_key: String,
    secret: String,
    passphrase: String,
    base_url: String,
}

impl OkxAccountClient {
    pub(crate) fn from_shared_http_instrument(
        instrument_type: impl Into<String>,
        http: PublicHttpClient,
        async_http: AsyncPublicHttpClient,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let instrument_type = instrument_type.into();
        if !matches!(
            instrument_type.as_str(),
            "SPOT" | "MARGIN" | "SWAP" | "FUTURES" | "OPTION"
        ) {
            return Err(ExchangeError::InvalidRequest(format!(
                "unsupported OKX instType: {instrument_type}"
            )));
        }
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
            http,
            async_http,
            instrument_type,
            api_key,
            secret,
            passphrase,
            base_url,
        })
    }

    pub(crate) fn with_instrument_type(mut self, instrument_type: impl Into<String>) -> Self {
        self.instrument_type = instrument_type.into();
        self
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
            .and_then(check_okx_response)
    }

    async fn get_async(
        &self,
        path: &str,
        query: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
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
        self.async_http
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.base_url, path),
                query,
                &headers,
            )
            .await
            .map(|response| response.body)
            .and_then(check_okx_response)
    }

    pub(crate) fn balance(&self) -> Result<Value, ExchangeError> {
        self.get("/api/v5/account/balance", &[])
    }

    pub(crate) async fn balance_async(&self) -> Result<Value, ExchangeError> {
        self.get_async("/api/v5/account/balance", &[]).await
    }

    pub(crate) fn positions(&self) -> Result<Value, ExchangeError> {
        self.get(
            "/api/v5/account/positions",
            &[("instType", self.instrument_type.clone())],
        )
    }

    pub(crate) async fn positions_async(&self) -> Result<Value, ExchangeError> {
        self.get_async(
            "/api/v5/account/positions",
            &[("instType", self.instrument_type.clone())],
        )
        .await
    }

    pub(crate) fn pending_orders(&self) -> Result<Value, ExchangeError> {
        self.get(
            "/api/v5/trade/orders-pending",
            &[("instType", self.instrument_type.clone())],
        )
    }

    pub(crate) async fn pending_orders_async(&self) -> Result<Value, ExchangeError> {
        self.get_async(
            "/api/v5/trade/orders-pending",
            &[("instType", self.instrument_type.clone())],
        )
        .await
    }

    pub(crate) fn order_history(&self, query: &ExternalOrderQuery) -> Result<Value, ExchangeError> {
        self.get(
            "/api/v5/trade/orders-history",
            &query_params(&self.instrument_type, query),
        )
    }

    pub(crate) async fn order_history_async(
        &self,
        query: &ExternalOrderQuery,
    ) -> Result<Value, ExchangeError> {
        self.get_async(
            "/api/v5/trade/orders-history",
            &query_params(&self.instrument_type, query),
        )
        .await
    }

    pub(crate) fn order_detail(&self, query: &ExternalOrderQuery) -> Result<Value, ExchangeError> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| ExchangeError::InvalidRequest("order_id is required".into()))?;
        let mut params = query_params(&self.instrument_type, query);
        params.push(("ordId", order_id.into()));
        self.get("/api/v5/trade/order", &params)
    }

    pub(crate) async fn order_detail_async(
        &self,
        query: &ExternalOrderQuery,
    ) -> Result<Value, ExchangeError> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| ExchangeError::InvalidRequest("order_id is required".into()))?;
        let mut params = query_params(&self.instrument_type, query);
        params.push(("ordId", order_id.into()));
        self.get_async("/api/v5/trade/order", &params).await
    }

    pub(crate) fn order_open(&self, query: &ExternalOrderQuery) -> Result<Value, ExchangeError> {
        let mut params = query_params(&self.instrument_type, query);
        if let Some(symbol) = &query.symbol {
            params.push(("instId", symbol.to_ascii_uppercase()));
        }
        self.get("/api/v5/trade/orders-pending", &params)
    }

    pub(crate) async fn order_open_async(
        &self,
        query: &ExternalOrderQuery,
    ) -> Result<Value, ExchangeError> {
        let mut params = query_params(&self.instrument_type, query);
        if let Some(symbol) = &query.symbol {
            params.push(("instId", symbol.to_ascii_uppercase()));
        }
        self.get_async("/api/v5/trade/orders-pending", &params)
            .await
    }

    pub(crate) fn trade_fee(&self, instrument: &str) -> Result<Value, ExchangeError> {
        self.get(
            "/api/v5/account/trade-fee",
            &[
                ("instType", self.instrument_type.clone()),
                ("instId", instrument.into()),
            ],
        )
    }

    pub(crate) async fn trade_fee_async(&self, instrument: &str) -> Result<Value, ExchangeError> {
        self.get_async(
            "/api/v5/account/trade-fee",
            &[
                ("instType", self.instrument_type.clone()),
                ("instId", instrument.into()),
            ],
        )
        .await
    }

    pub(crate) fn config(&self) -> Result<Value, ExchangeError> {
        self.get("/api/v5/account/config", &[])
    }

    pub(crate) async fn config_async(&self) -> Result<Value, ExchangeError> {
        self.get_async("/api/v5/account/config", &[]).await
    }

    pub(crate) fn submit_order(&self, body: Value) -> Result<Value, ExchangeError> {
        self.post("/api/v5/trade/order", body)
    }

    pub(crate) fn cancel_order(&self, body: Value) -> Result<Value, ExchangeError> {
        self.post("/api/v5/trade/cancel-order", body)
    }

    pub(crate) async fn submit_order_async(&self, body: Value) -> Result<Value, ExchangeError> {
        self.post_async("/api/v5/trade/order", body).await
    }

    pub(crate) async fn cancel_order_async(&self, body: Value) -> Result<Value, ExchangeError> {
        self.post_async("/api/v5/trade/cancel-order", body).await
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

    async fn post_async(&self, path: &str, body: Value) -> Result<Value, ExchangeError> {
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
        self.async_http
            .post_json_command_with_headers(&format!("{}{}", self.base_url, path), &headers, &body)
            .await
            .map(|response| response.body)
            .and_then(check_okx_response)
    }
}

fn query_params(instrument_type: &str, query: &ExternalOrderQuery) -> Vec<(&'static str, String)> {
    let mut values = vec![("instType", instrument_type.into())];
    if let Some(symbol) = &query.symbol {
        values.push(("instId", symbol.to_ascii_uppercase()));
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    values
}

pub(crate) fn normalize_okx_orders(value: &Value) -> Result<Vec<ExternalOrder>, String> {
    value
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX order query data is missing".to_string())?
        .iter()
        .map(normalize_okx_order)
        .collect()
}
pub(crate) fn normalize_okx_order(value: &Value) -> Result<ExternalOrder, String> {
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
        binding_id: String::new(),
        order_id: OrderId::try_from(order_id).map_err(|error| error.to_string())?,
        client_order_id: text("clOrdId")
            .map(ClientOrderId::try_from)
            .transpose()
            .map_err(|error| error.to_string())?,
        symbol: Symbol::try_from(text("instId").unwrap_or_else(|| "UNKNOWN".into()))
            .map_err(|error| error.to_string())?,
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
        status: crate::application::capabilities::execution_facts::normalize_order_status(
            &text("state").unwrap_or_else(|| "UNKNOWN".into()),
        ),
        quantity,
        filled_quantity,
        average_fill_price,
        occurred_at_unix_millis: value
            .get("uTime")
            .and_then(Value::as_str)
            .and_then(|v| v.parse::<u64>().ok())
            .map(|value| UnixNanos::from(value.saturating_mul(1_000))),
    })
}

fn order_decimal(
    value: crate::application::capabilities::account_facts::ExternalDecimal,
) -> crate::application::capabilities::DecimalValue {
    crate::application::capabilities::DecimalValue::new(value.mantissa, value.scale)
}

pub(crate) fn order_request_body(
    request: &OrderEntryRequest,
    trading_mode: &str,
) -> Result<Value, IntegrationError> {
    let mut body = serde_json::Map::from_iter([
        (
            "instId".into(),
            Value::String(symbol(request).map_err(IntegrationError::InvalidRequest)?),
        ),
        ("tdMode".into(), Value::String(trading_mode.into())),
        ("side".into(), Value::String(side(request.side).into())),
        ("ordType".into(), Value::String(order_type(request).into())),
        (
            "sz".into(),
            Value::String(format_entry_decimal(request.quantity)),
        ),
        (
            "clOrdId".into(),
            Value::String(request.order_id.to_string()),
        ),
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
    Ok(Value::Object(body))
}

pub(crate) fn normalize_order_submission(
    request: &OrderEntryRequest,
    payload: &Value,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    let Some(row) = payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
    else {
        return Ok(CommandOutcome::Indeterminate(
            IndeterminateCommand::may_have_been_sent("OKX order response data is missing"),
        ));
    };
    let code = row.get("sCode").and_then(Value::as_str).unwrap_or("0");
    if code != "0" {
        return Ok(CommandOutcome::Rejected(ProviderRejection {
            code: Some(code.into()),
            message: row
                .get("sMsg")
                .and_then(Value::as_str)
                .unwrap_or("OKX rejected the order")
                .into(),
            provider_request_id: None,
        }));
    }
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status: OrderEntryStatus::Accepted,
        remote_order_id: row
            .get("ordId")
            .and_then(Value::as_str)
            .and_then(|value| kairos_primitives::RemoteOrderId::new(value).ok()),
        filled_quantity: None,
        occurred_at_unix_nanos: now_nanos().into(),
        reason: row
            .get("sMsg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    }))
}

pub(crate) fn cancel_request_body(
    request: &OrderEntryRequest,
    remote_order_id: &str,
) -> Result<Value, IntegrationError> {
    Ok(serde_json::json!({
        "instId": symbol(request).map_err(IntegrationError::InvalidRequest)?,
        "ordId": remote_order_id,
    }))
}

pub(crate) fn normalize_order_cancellation(
    request: &OrderEntryRequest,
    remote_order_id: &str,
    at_unix_nanos: u64,
    payload: &Value,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    let row = payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first());
    let code = row
        .and_then(|value| value.get("sCode"))
        .and_then(Value::as_str)
        .unwrap_or("0");
    if code != "0" {
        return Ok(CommandOutcome::Rejected(ProviderRejection {
            code: Some(code.into()),
            message: row
                .and_then(|value| value.get("sMsg"))
                .and_then(Value::as_str)
                .unwrap_or("OKX rejected the cancellation")
                .into(),
            provider_request_id: None,
        }));
    }
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status: OrderEntryStatus::Canceled,
        remote_order_id: kairos_primitives::RemoteOrderId::new(remote_order_id).ok(),
        filled_quantity: None,
        occurred_at_unix_nanos: at_unix_nanos.into(),
        reason: row
            .and_then(|value| value.get("sMsg"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    }))
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

pub(crate) fn normalize_market_profile(
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
            .map(kairos_primitives::Currency::new)
            .transpose()
            .map_err(|error| error.to_string())?,
        fee_discount: None,
        fee_tier: fee_row
            .get("feeGroup")
            .and_then(Value::as_str)
            .map(str::to_owned),
        source: "okx".into(),
        observed_at_unix_nanos: now_nanos().into(),
    })
}

pub(crate) fn normalize_account(
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
                asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}")).ok()?,
                asset_code: kairos_primitives::Currency::new(code).ok()?,
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
            let provider_instrument = external_instrument_ref(
                crate::domain::ParticipantKind::Exchange,
                "okx",
                item.get("instType")
                    .and_then(Value::as_str)
                    .unwrap_or("okx"),
                symbol,
            )
            .ok()?;
            Some(Position {
                provider_instrument,
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
        observed_at_unix_nanos: now_nanos().into(),
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

pub(crate) fn normalize_credential_profile(
    payload: &Value,
    segment: &str,
) -> Result<ExternalAccountCredentialProfile, String> {
    let row = payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX account config data is missing".to_string())?;
    let account_type = row.get("acctLv").and_then(Value::as_str).map(str::to_owned);
    let remote_identity = row.get("uid").and_then(Value::as_str).map(str::to_owned);
    let mut attributes = std::collections::BTreeMap::new();
    if let Some(value) = row.get("posMode").and_then(Value::as_str) {
        attributes.insert("position_mode".into(), value.into());
    }
    Ok(ExternalAccountCredentialProfile {
        remote_identity,
        account_type,
        permissions: vec!["read".into()],
        segments: vec![segment.into()],
        attributes,
    })
}

fn normalize_open_order(value: &Value) -> Result<OpenOrder, String> {
    let remote_order_id = value
        .get("ordId")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX pending order id is missing".to_string())?;
    let symbol = value
        .get("instId")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX pending order instrument is missing".to_string())?;
    let provider_instrument = external_instrument_ref(
        crate::domain::ParticipantKind::Exchange,
        "okx",
        value
            .get("instType")
            .and_then(Value::as_str)
            .unwrap_or("okx"),
        symbol,
    )?;
    let local_order_id = value
        .get("clOrdId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(remote_order_id);
    Ok(OpenOrder {
        order_id: kairos_primitives::OrderId::new(local_order_id)?,
        remote_order_id: Some(kairos_primitives::RemoteOrderId::new(remote_order_id)?),
        provider_instrument,
        side: crate::application::capabilities::execution_facts::normalize_order_side(
            value
                .get("side")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
        quantity: decimal_field(value, "sz")?,
        filled_quantity: decimal_field(value, "accFillSz").unwrap_or_default(),
        status: crate::application::capabilities::execution_facts::normalize_order_status(
            value
                .get("state")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
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
    DecimalValue::parse(value)
}

fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    if request.provider_instrument.participant.id.as_str() != "okx" {
        return Err(format!(
            "OKX order received provider instrument for {}",
            request.provider_instrument.participant.id
        ));
    }
    Ok(request
        .provider_instrument
        .source_symbol
        .as_str()
        .replace('/', "-")
        .to_ascii_uppercase())
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

fn format_entry_decimal(value: crate::application::capabilities::DecimalValue) -> String {
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
    use super::{normalize_account, normalize_market_profile, order_request_body};
    use crate::application::capabilities::account_facts::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };
    use crate::application::ExternalMarketProfileRequest as AccountMarketProfileRequest;

    #[test]
    fn order_uses_provider_instrument_instead_of_parsing_market_id() {
        let request = crate::application::capabilities::OrderEntryRequest {
            order_id: kairos_primitives::OrderId::new("order-1").unwrap(),
            intent_id: None,
            account_id: kairos_primitives::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("swap").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc-swap").unwrap(),
            market_id: Some(
                kairos_primitives::MarketId::new("market:canonical:not-an-okx-symbol").unwrap(),
            ),
            provider_instrument: crate::domain::ProviderInstrumentRef::new(
                crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "okx")
                    .unwrap(),
                Some(crate::application::participants::okx::InstrumentType::Swap.into()),
                "BTC-USDT-SWAP",
            )
            .unwrap(),
            side: crate::application::capabilities::OrderSide::Buy,
            quantity: crate::application::capabilities::DecimalValue::new(1, 0),
            order_type: crate::application::capabilities::OrderType::Market,
            limit_price: None,
            options: Default::default(),
        };
        let body = order_request_body(&request, "cross").unwrap();
        assert_eq!(body["instId"], "BTC-USDT-SWAP");
    }

    #[test]
    fn normalizes_okx_balance_and_short_position() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("okx", "main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("swap").unwrap(),
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
            account_id: kairos_primitives::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("swap").unwrap(),
            market_id: kairos_primitives::MarketId::new("market:okx:BTC-USDT-SWAP").unwrap(),
            source_symbol: kairos_primitives::Symbol::new("BTC-USDT-SWAP").unwrap(),
        };
        let result = normalize_market_profile(
            &request,
            &serde_json::json!({"code":"0","data":[{"maker":"-0.0002","taker":"0.0005","feeCcy":"USDT","feeGroup":"1"}]}),
            &serde_json::json!({"code":"0","data":[{"acctLv":"3","posMode":"long_short_mode"}]}),
        )
        .unwrap();
        assert_eq!(
            result.account_model,
            Some(crate::application::capabilities::account_facts::ExternalAccountModel::Unified)
        );
        assert_eq!(result.position_mode.as_deref(), Some("long_short_mode"));
        assert_eq!(result.fee_currency.as_deref(), Some("USDT"));
    }
}
