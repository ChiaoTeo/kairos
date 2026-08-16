use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::account_facts::{
    external_instrument_ref, ExternalAccountModel as AccountModel,
    ExternalAccountSegment as AccountSegment, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalOpenOrder as OpenOrder, ExternalPosition as Position,
};
use serde_json::Value;

use crate::application::{
    AccountCredentialInspectionConnection, AccountReadConnection, ExternalAccountCredentialProfile,
    IntegrationError,
};
use crate::services::participants::binance::signing::signed_query;
use crate::services::participants::binance::spot::runtime::{
    BinanceRequestRuntime, QuotaAllocation, RequestPriority,
};
use crate::services::transport::http::{AsyncPublicHttpClient, ExchangeError, PublicHttpClient};

#[derive(Clone)]
pub(crate) struct BinanceOptionsAccountClient {
    http: PublicHttpClient,
    async_http: AsyncPublicHttpClient,
    api_key: String,
    secret: String,
    base_url: String,
    clock_offset_millis: Arc<tokio::sync::Mutex<Option<i64>>>,
    runtime: BinanceRequestRuntime,
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
        let http = PublicHttpClient::new("kairos-integration/binance-options-account")?;
        let runtime = BinanceRequestRuntime::new(
            http.clone(),
            QuotaAllocation {
                request_weight_per_minute: 6_000,
                cancel_reserve_weight: 100,
            },
        )?;
        Self::from_runtime(runtime, api_key, secret, base_url)
    }

    pub(crate) fn from_runtime(
        runtime: BinanceRequestRuntime,
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
            http: runtime.http(),
            async_http: runtime.async_http(),
            api_key,
            secret,
            base_url,
            clock_offset_millis: Arc::new(tokio::sync::Mutex::new(None)),
            runtime,
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
        let signed = signed_query(&self.secret, params)?;
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

    pub(crate) async fn request_async(
        &self,
        path: &str,
        params: BTreeMap<String, String>,
        method: Method,
    ) -> Result<Value, ExchangeError> {
        for attempt in 0..2 {
            self.runtime.acquire(
                1,
                match method {
                    Method::Get => RequestPriority::Reconciliation,
                    Method::Post => RequestPriority::NewOrder,
                    Method::Delete => RequestPriority::Cancel,
                },
            )?;
            let mut values = params.clone();
            values.insert(
                "timestamp".into(),
                self.timestamp_async().await?.to_string(),
            );
            values.insert("recvWindow".into(), "5000".into());
            let signed = signed_query(&self.secret, values)?;
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
            let result = match method {
                Method::Get => {
                    self.async_http
                        .get_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                        .await
                }
                Method::Post => {
                    self.async_http
                        .post_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                        .await
                }
                Method::Delete => {
                    self.async_http
                        .delete_json_response_with_headers_and_query(&endpoint, &refs, &headers)
                        .await
                }
            };
            match result {
                Ok(response) => return Ok(response.body),
                Err(error)
                    if attempt == 0
                        && matches!(method, Method::Get)
                        && timestamp_rejection(&error) =>
                {
                    *self.clock_offset_millis.lock().await = None;
                }
                Err(error) => return Err(error),
            }
        }
        unreachable!("bounded Binance Options timestamp retry loop")
    }

    async fn timestamp_async(&self) -> Result<u64, ExchangeError> {
        let mut offset = self.clock_offset_millis.lock().await;
        if let Some(value) = *offset {
            return apply_offset(now_millis(), value);
        }
        let started = now_millis();
        self.runtime.acquire(1, RequestPriority::Background)?;
        let payload = self
            .async_http
            .get_json(&format!("{}/eapi/v1/time", self.base_url))
            .await
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let completed = now_millis();
        let server = payload
            .get("serverTime")
            .and_then(Value::as_u64)
            .ok_or_else(|| ExchangeError::Preflight("Options serverTime is missing".into()))?;
        let midpoint = started.saturating_add(completed.saturating_sub(started) / 2);
        let value = i64::try_from(server)
            .unwrap_or(i64::MAX)
            .saturating_sub(i64::try_from(midpoint).unwrap_or(i64::MAX));
        *offset = Some(value);
        apply_offset(now_millis(), value)
    }

    pub(crate) async fn listen_key_async(&self) -> Result<String, ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        let endpoint = format!("{}/eapi/v1/listenKey", self.base_url);
        self.async_http
            .post_json_response_with_headers_and_query(
                &endpoint,
                &[],
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )
            .await?
            .body
            .get("listenKey")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| ExchangeError::InvalidRequest("Options listen key is missing".into()))
    }

    pub(crate) async fn keepalive_listen_key_async(
        &self,
        listen_key: &str,
    ) -> Result<(), ExchangeError> {
        self.runtime.acquire(1, RequestPriority::Background)?;
        self.async_http
            .put_query_json_response_with_headers_and_query(
                &format!("{}/eapi/v1/listenKey", self.base_url),
                &[("listenKey", listen_key.to_owned())],
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )
            .await
            .map(|_| ())
    }
}

#[derive(Clone, Copy)]
pub(crate) enum Method {
    Get,
    Post,
    Delete,
}

fn apply_offset(local: u64, offset: i64) -> Result<u64, ExchangeError> {
    if offset >= 0 {
        Ok(local.saturating_add(offset as u64))
    } else {
        local
            .checked_sub(offset.unsigned_abs())
            .ok_or_else(|| ExchangeError::Preflight("invalid Options clock offset".into()))
    }
}

fn timestamp_rejection(error: &ExchangeError) -> bool {
    matches!(error, ExchangeError::Http { body, .. } if serde_json::from_str::<Value>(body).ok().and_then(|value| value.get("code").and_then(Value::as_i64)) == Some(-1021))
}

pub struct BinanceOptionsAccountConnection {
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
        Ok(Self { client })
    }
}

impl AccountReadConnection for BinanceOptionsAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &AccountSegment,
    ) -> Result<AccountSnapshot, IntegrationError> {
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

pub(crate) fn normalize_account(
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
                asset_id: kairos_domain_types::AssetId::new(format!("asset:crypto:{code}")).ok()?,
                asset_code: kairos_domain_types::Currency::new(code).ok()?,
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
            let provider_instrument = external_instrument_ref(
                crate::domain::ParticipantKind::Exchange,
                "binance",
                "binance-options",
                symbol,
            )
            .ok()?;
            Some(Position {
                provider_instrument,
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
        .map(|value| normalize_open_order(value, "binance-options"))
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
        account_model: Some(AccountModel::Contract),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn normalize_open_order(value: &Value, product: &str) -> Result<OpenOrder, String> {
    let remote_order_id = value
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
    let provider_instrument = external_instrument_ref(
        crate::domain::ParticipantKind::Exchange,
        "binance",
        product,
        symbol,
    )?;
    let local_order_id = value
        .get("clientOrderId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or(&remote_order_id);
    Ok(OpenOrder {
        order_id: kairos_domain_types::OrderId::new(local_order_id)?,
        remote_order_id: Some(kairos_domain_types::RemoteOrderId::new(remote_order_id)?),
        provider_instrument,
        side: crate::application::capabilities::execution_facts::normalize_order_side(
            value
                .get("side")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
        quantity: decimal_field(value, "quantity").unwrap_or_default(),
        filled_quantity: decimal_field(value, "executedQty").unwrap_or_default(),
        status: crate::application::capabilities::execution_facts::normalize_order_status(
            value
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        ),
    })
}

fn decimal_field(row: &Value, field: &str) -> Option<DecimalValue> {
    row.get(field)
        .and_then(Value::as_str)
        .and_then(|value| decimal(value).ok())
}
fn decimal(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
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
    use crate::application::capabilities::account_facts::{
        ExternalAccountIdentity, ExternalAccountSegment as AccountSegment,
    };
    #[test]
    fn normalizes_options_assets_and_positions() {
        let segment = AccountSegment {
            identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("options").unwrap(),
            environment: "live".into(),
            account_model: Some("contract".into()),
        };
        let snapshot = normalize_account(&segment, &serde_json::json!({"assets":[{"asset":"USDT","marginBalance":"1000","available":"900"}],"positions":[{"symbol":"BTC-250101-60000-C","quantity":"1","averagePrice":"100"}]}), &serde_json::json!([])).unwrap();
        assert_eq!(snapshot.positions.len(), 1);
        assert_eq!(snapshot.balances[0].asset_code, "USDT");
    }
}
